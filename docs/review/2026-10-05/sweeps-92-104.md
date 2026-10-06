# Sweeps of classes 92 to 104, 2026-10-06

Tree: branch `review/2026-10-05`, uncommitted fix-round tree.
Scripts and raw outputs: `~/tt-data/mcuscope-tools/sweeps/class-<n>/` (`sweep.sh`, `out.txt`; class 100 has `extract.py` and `examples.txt`).
Owner rulings read from `triage.md`; a ruled behaviour is marked "exempt because ruled".
No test suite run and no daemon started; two probes ran in-process (named where used).

| Class | Sites | Violations |
|---|---|---|
| 92 | 127 | 3 |
| 93 | 25 | 2 |
| 94 | 19 (+3 read beyond the grep) | 4 |
| 95 | 27 | 3 |
| 96 | 16 (+ structural reads) | 2 |
| 97 | 11 | 0 |
| 98 | 43 | 2 |
| 99 | 21 | 0 |
| 100 | 142 | not run (blocked, see the question) |
| 101 | 17 | 0 |
| 102 | 9 | 0 |
| 103 | 68 | 1 |
| 104 | 201 | 2 |

## Question for the orchestrator

Class 100's sweep is "run every example on an isolated `mcuscoped --sim`", and the brief says start no daemon.
Recommended: allow one isolated `mcuscoped --sim --config <throwaway TOML with its own db_path and port>` after the whole-suite run ends, and rerun class 100 then.
Alternative: rule class 100 from the tests that already execute guide examples, which needs a mapping nobody has made.

## 92. A guard keyed on a path's spelling

`grep -n "db_path\|\.lock\b\|\.pid\b" host/mcuscope/*.py`: 127 sites.

### Violations

- **V92-1** `server.py:3004-3019` (`_export_dir`, `_export_key`), used at `:539-541` and `:3219`.
  - Failure: the export temp key is `sha256(abspath(db_path))` and the temp dir is the spelled path's parent, not the file's.
    Two daemons started on one spelling (a symlink retargeted between the starts) lock different files (the lock is on `realpath`) but share key and dir, so the second's startup `_sweep_export_orphans` deletes the first's live export temps.
    A spelling change across restarts leaves the old run's orphans unswept.
  - Fix: compute the key and the dir from `os.path.realpath(db_path)` once in the lifespan (off the loop), the identity the capture lock already uses.
- **V92-2** `server.py:1177-1185` (`_same_path`), used at `:1457` and `:1549`.
  - Failure: `restart_required` compares `normcase(normpath())` spellings, so a symlink or another spelling of the running capture reports a restart that changes nothing.
    (A retargeted symlink under the same spelling is caught by the store's inode check, so only the false positive remains.)
  - Fix: compare `realpath` of both sides, inside the existing `to_thread` for GET and off the loop for PUT.
- **V92-3** `store.py:1036-1042` (`_open_writer`), latent.
  - Failure: the identity is `os.stat(path)` taken after `sqlite3.connect`, not of the opened handle; a replace between connect and stat records the replacement's inode while the writer holds the original, and every tick check then passes.
  - Fix: stat before connect and again after `_setup_writer`, and refuse to start (CaptureUnreadable-style message) when they differ.

### Verdict list

- `cli.py:214`: exempt because it prints the status field, no guard.
- `cli.py:2837, 2839, 2850, 2890, 2894, 2916, 2924, 2925, 2929, 2930, 2933`: exempt because `.pid\b` matches `proc.pid`, a process attribute; the pid record removals at 2839 and 2916 go through `pidfile.remove_record_if`, which judges by content (class 7).
- `cli_daemonctl.py:57`: exempt because it derives the `.err` name from the pid record name, no guard.
- `cli_daemonctl.py:110`: exempt, comment.
- `cli_daemonctl.py:325, 328, 331, 348, 352, 358`: exempt, `proc.pid` (as above).
- `config.py:91, 146, 156, 157, 160, 296, 346, 434, 658, 664`: complies; `resolve_db_path` yields the absolute spelling and every guard downstream resolves identity itself (lock `realpath`, store inode).
- `daemon.py:34, 197, 225, 354`: exempt, import, startup notice and docstrings.
- `daemon.py:467, 468, 469, 471`: complies; the lock is built from the resolved path and skipped for `:memory:` (MODULES-2).
- `lockfile.py:6, 113, 145, 201`: exempt, docstring, message and holder record text.
- `lockfile.py:23`: complies; the docstring says hard links are not covered, as the class asks.
- `lockfile.py:126, 127, 128`: complies; lock keyed on `realpath(db_path) + ".lock"`, then `_is_current` compares `fstat(fd)` with `stat(path)` at acquire and on each tick (`verify`, registered at `server.py:489`).
- `pidfile.py:18`: exempt, docstring.
- `pidfile.py:66`: exempt because the record is keyed by host:port by design (class 7), not by a file it guards.
- `server.py:77, 387`: exempt, import and body field.
- `server.py:485`: complies; the store does its own inode check.
- `server.py:539, 541, 3004, 3010, 3012, 3016, 3019, 3029, 3031, 3051, 3053, 3079, 3219`: violates, V92-1.
- `server.py:1269, 1466`: exempt, reported values.
- `server.py:1457, 1549`: violates, V92-2.
- `server.py:1516, 1517, 1518, 1526, 1532, 1547`: exempt, validation and save of the setting, no guard.
- `server.py:1915, 1922, 1926, 1949`: complies; a fresh `mkstemp` file, held by its own name only.
- `server.py:4132, 4133`: exempt, USB `info.pid`.
- `store.py:731, 732`: exempt, constructor.
- `store.py:833, 846, 849, 852`: complies; `_check_path` compares `(st_dev, st_ino)` with the start identity.
- `store.py:864`: complies; in-memory capture skips the identity check.
- `store.py:875, 903, 904, 925, 953, 956`: complies; failure messages of `_fail_capture`, which stops writing on an identity mismatch.
- `store.py:943`: complies; `_move_wal_aside` renames a side file only when its own inode matches the one recorded.
- `store.py:981, 982`: exempt, message.
- `store.py:1027, 1028, 1029, 1035`: complies (creation, class 98).
- `store.py:1036, 1045`: violates, V92-3.
- `store.py:1051, 1054, 1056`: complies; `_file_identity`.
- `store.py:1079, 1084, 1091, 1095, 1117, 1123, 1490, 1506, 3810`: exempt, log text.
- `store.py:2110`: complies; `_check_path` runs immediately before the ATTACH by path.
- `store.py:2648, 2717, 3273, 3319, 3335, 3374, 3380, 3793`: exempt, in-memory checks.
- `store.py:2675, 3391`: complies; each read connection opens by path right after `_check_path` (a microsecond residual), and a cached one keeps its inode by handle.
- `store.py:3798, 3826`: exempt because they are size reads for reporting; after a replace `capture_error` is already set.

## 93. An episode notice written through the path whose failure it reports

`grep -n "_EpisodeNotice\|_spawn_sys\|_submit_notice" host/mcuscope/*.py`: 25 sites.

### Violations

- **V93-1** `serial_link.py:927-950` (`_drop_rx_line`, `_close_unstorable`) with `stop()` at `:431`.
  - Failure: an unstorable-line episode still open at detach, reconnect or shutdown is never closed: no sys row, and no closing log line (the docstring's "Logged once, then at close" does not hold).
    `_unstorable_n` is not carried across reattach, so the episode is lost for good.
    `_close_unstorable` also calls `_spawn_sys` without `stopping=True`, so calling it from `stop()` as written would be dropped.
  - Fix: in `stop()`, when `_unstorable_n`, call `_close_unstorable` with the row spawned `stopping=True` (its log line runs regardless); or carry the episode in `_carried`.
- **V93-2** `serial_link.py:466-483` (`stop()`'s stranded-lines row).
  - Failure: the lines left in the rx queue are recorded only by a sys row written through the store that failed to take them (stop runs on store backpressure); `stop()` then cancels pending sys rows after 2 s (`:488-494`), and a row cancelled while waiting on a full write queue is lost with no log line anywhere. `rx_dropped` resets on restart.
  - Fix: `log.warning` the same text before spawning the row.

### Verdict list

- `serial_link.py:276`: exempt, class definition.
- `serial_link.py:361`: complies; rx overflow comes from a slow store, and the row lands when it catches up; counted in `rx_dropped`.
- `serial_link.py:368, 1051`: complies; a decode failure, the store is healthy.
- `serial_link.py:397`: exempt, comment.
- `serial_link.py:398, 399, 400, 401`: complies; data-shape episodes (unterminated, oversized, replaced, name overflow) whose rows go through a healthy store, each counted.
- `serial_link.py:428, 709, 731, 752, 787`: complies; link-state rows, not store-failure notices.
- `serial_link.py:482`: violates, V93-2.
- `serial_link.py:675`: exempt, definition of `_spawn_sys`.
- `serial_link.py:809, 839, 849, 863, 996`: complies (as for 398-401 and 361).
- `serial_link.py:950`: violates, V93-1 (the row is right when a line stores again; the stop path is missing).
- `serial_link.py:1085`: exempt, comment in `_store_sys`.
- `store.py:993`: complies; RES-7's notice, queued at start into an empty queue.
- `store.py:1145`: complies; `_submit_notice`.

Read beyond the grep: the write-lock episode (`store.py:1482-1510`) is recorded when it clears (complies); `_fail_capture` (`store.py:874`) is exempt because the capture file is gone or replaced, so nothing of it can be written, and `/status` `capture_error` plus the log say why.

## 94. Captured data deleted or altered with no counter or notice

`sweep.sh` (the `DELETE` grep of `store.py` plus the transform grep of `serial_link.py`, `protocol.py`, `firmware/monitor/*.c`): 19 sites.
Read beyond the grep: every caller of `_delete_lines` (`store.py:2177`), and `monitor.c:1097` (the DLC clamp the triage named).

### Violations

- **V94-1** `store.py:3573-3581` (`_trim_oldest`) and `:3611-3648` (`_sweep_size_locked`).
  - Failure: chunks commit one at a time, but `dropped` is added to `lines_trimmed` and announced only after the loop; an exception mid-loop or a cancel loses the count of rows already deleted.
    With the writer's busy timeout now 5 ms (RES-1), another process's write lock makes a mid-sweep "database is locked" routine; a stop during the startup sweep cancels it.
  - Fix: accumulate in a `finally` as the age sweep does, and write the sys row for a partial trim too.
- **V94-2** `store.py:3709-3735` (`_sweep_retention_locked`, `_sweep_retention_reported`).
  - Failure: `lines_expired` is counted in the `finally`, but the sys row is written only on a normal return, so a failed or cancelled sweep deletes rows that no capture row records, and the counter resets on restart.
  - Fix: keep an unannounced count and announce it in the `finally` or on the next sweep that runs.
- **V94-3** `store.py:3523-3537` (`_delete_chunks`: purge, `DELETE /sessions/{id}?data=true`).
  - Failure: a failure after some chunks answers 500 with no count, while those rows are gone; the success-only `log.warning` at `server.py:1998` and `:2014` does not run, so nothing records how many were deleted.
  - Fix: log the partial count in a `finally`, and name it in the error.
- **V94-4** `firmware/monitor/monitor.c:1097` (data-frame DLC clamp), owner ruling open.
  - Failure: a data frame with DLC 9..15 is emitted as 8 data bytes; the DLC code is lost, uncounted, and unannounced (stated in INTEGRATION.md:317 only).
    The OP-3 ruling says "the dlc clamp follows the same rule"; `fix-firmware.md:49` applied it to RTR only and asked the owner, and no answer is recorded.
  - Fix, if the owner rules it a violation: an `!e can dlc <n> clamped` notice latched once after init (as the bus-drop notice), or carry the DLC as RTR does.

### Verdict list

- `store.py:80, 92`: complies; schema cascades, reached only through the counted line deletes.
- `store.py:112, 212, 3557, 3654, 3687`: exempt, comments, constant and docstrings.
- `store.py:1663`: complies; removes a line inserted in the same failed transaction, and the line is then failed and counted (`write_errors` or `rx_dropped`).
- `store.py:2072`: exempt because it deletes a session label; the docstring and route keep captured lines.
- `store.py:2192`: complies for its counted callers; violates through V94-1, V94-2 and V94-3.
- `serial_link.py:556, 561, 562, 878`: exempt, backoff and batch arithmetic, not data.
- `serial_link.py:836`: complies; ASCII replacement counted in `rx_replaced` with a sys row per episode (ruled OP-5).
- `protocol.py:110`: exempt, a length check on outgoing text.
- `monitor.c:107, 142, 976`: exempt, lossless nibble extraction for hex output.

Not filed, for the owner: `fold_breaks` (`store.py:246`) rewrites CR and LF inside a received line to a space, uncounted. SPEC 2.1 and 3.4 state it as the storage form, so it is not silent in the CLAUDE.md sense, but it is the same shape as the FUZZ-5 replacement, which the owner chose to count.

## 95. Daemon state keyed by device-chosen names with no cardinality cap

`sweep.sh` (instance and module dicts, sets and deques in the daemon-side modules): 27 sites.
The first version missed a multi-line annotation (`_carried`); the kept script matches it.

### Violations

- **V95-1** `store.py:811` (`_plot_summary`), via `serial_link.py:985-1000` and `store.py:2898-2899`.
  - Failure: the OP-2 cap covers ad-hoc `!p` names only. Typed names (`!pd`/`!ps`) enter the summary as `(port, name)` keys too, `_keep_recent_adhoc` prunes only `sid is None`, and the decoder counts only `!p` names; a device redefining one sid with fresh field names grows the summary, and `/plot/channels`, without bound.
  - Fix: count typed names against the same per-port cap (refuse a `!pd` that would pass it, counted in `plot_name_refused`, announced once), and prune typed keys in the rebuild as ad-hoc ones are. OP-2 was asked about ad-hoc names, so the owner may want to confirm the scope.
- **V95-2** `server.py:856` (`_fails`), eviction at `:888-891`.
  - Failure: bounded at `TOKEN_FAIL_TABLE_MAX`, but reaching the bound evicts live lockouts with no counter and no log, so an address spray that clears lockouts is invisible.
  - Fix: log once per eviction episode (and/or count it on `/status`).
- **V95-3** `serial_link.py:1423` (`_carried`), eviction at `:1529-1534`, nit.
  - Failure: at `CARRIED_MAX` the oldest alias's carried counters (`rx_dropped`, the ad-hoc name set) are dropped silently, so that alias reattaches with zeroed drop counts and a fresh name budget.
  - Fix: log the eviction.

### Verdict list

- `server.py:538`: complies; export temps, bounded by `EXPORT_WORKERS + EXPORT_QUEUE_MAX` builds.
- `server.py:856`: violates, V95-2.
- `server.py:2991, 3799`: exempt, fixed pool names and a constant table.
- `store.py:755`: exempt, two fixed keys.
- `store.py:766, 774, 777`: complies; keyed by subscriber queue, capped by `MAX_SUBSCRIBERS`, refusal answered 503 or WS 1013.
- `store.py:811`: violates, V95-1.
- `store.py:817` (`_plot_evicted`): complies; per run at most `ADHOC_NAMES_MAX` new ad-hoc names per port enter it, the rest come from the stored capture, which retention bounds; the ceiling is noted in a `ponytail:` comment.
- `store.py:821`: exempt, one connection per worker thread.
- `serial_link.py:358`: complies; `RX_QUEUE_MAX`, shed rows counted and announced.
- `serial_link.py:362`: exempt, tasks the daemon spawns, not names.
- `serial_link.py:367`: exempt, keyed by the daemon's own seq.
- `serial_link.py:372`: complies; the OP-2 cap itself, counted and announced.
- `serial_link.py:411`: complies; capped at `MAX_ERR_NOTICES`, suppressions counted; keys are the daemon's error strings.
- `serial_link.py:1408`: complies; `MAX_PORTS`, refusal named to the client.
- `serial_link.py:1423`: violates, V95-3.
- `protocol.py:42, 46, 57, 613`: exempt, constants.
- `protocol.py:956`: complies; keyed by sid, which the grammar limits to `0-9`.
- `_stdio.py:193`: exempt, fixed stream names.
- `config.py:344`: exempt, constant.
- `sim.py:154, 597`: exempt, simulator constants.

## 96. An untrusted request body buffered or echoed whole

`grep -n "got\|input\|detail" host/mcuscope/server.py`: 16 sites.
Read beyond the grep, as the class sweep asks: every body model (all `_Body`, `extra="forbid"`), `_BodyLimit` (`server.py:981`), `ws_max_size` (`daemon.py:570`), and every error that formats client input.

### Violations

- **V96-1** `server.py:631-642` (`_validation_error`).
  - Failure: `where` comes from `loc`, which for an unknown field is the client's own key, and it is echoed uncut; the number of errors is unbounded.
    Probe (`MarkerBody.model_validate_json` in-process): a 70,903-byte body with 6000 unknown keys gives 6000 errors and a 280,888-byte detail; a 60,000-character key is echoed whole.
  - Fix: cut `where` to `_GOT_MAX` as `got` is, and report the first few errors plus "and N more".
- **V96-2** client strings echoed whole in error messages.
  - Sites: `server.py:1223`, `:1235` (via `_resolve_port`), `:1378`, `:1389`, `:1403` (port); `:1774`, `:1821`, `:1829`, `:1976`, `:3605`, `:3662` (session ref, the last two through `_http_error` at `:622`); `:2431` (WS close reason).
  - Failure: the value is bounded only by the 64 KB body cap or the URL limit, not excerpted.
    At `:2431` a `?port=` longer than about 110 characters makes the close reason exceed the 123-byte limit: probe (`websockets.frames.Frame(Opcode.CLOSE, ...).check()`) raises `ProtocolError: control frame too long`, so the 1008 refusal becomes a handler exception.
  - Fix: one helper that quotes a client value cut to `_GOT_MAX` (as `post_marker` does with `body.port[:64]` at `:2411`), and a WS reason kept under 123 bytes.

### Verdict list

- `server.py:131, 367, 851, 2921`: exempt, comments.
- `server.py:622`: violates, V96-2 (carries the `:3605`/`:3662` texts).
- `server.py:634, 635, 636, 638, 639`: complies; `got` is cut to 80 characters.
- `server.py:640, 641, 642`: violates, V96-1.
- `server.py:3548, 3549, 3550`: complies; device detail is bounded by the 255-byte line, and the timeout is an integer.
- `_BodyLimit` (`server.py:981-1040`): complies; refuses a declared length over the cap, counts streamed bytes whatever the framing.
- `daemon.py:570` `ws_max_size=MAX_BODY_BYTES`: complies.
- `server.py:3304` `bad regex {pat!r}`: complies; patterns are capped at `MAX_MATCH_LEN` (200) first.

## 97. A kept escape sequence whose state outlives its line

`sweep.sh` (every `visible(` plus every raw write of `raw`/`text` in the CLI modules): 11 sites.

### Verdict list

- `cli_output.py:58`: complies; a line whose last kept SGR is not a reset gets `\x1b[0m` before its LF or at the end; every other control byte is escaped.
- `cli_output.py:80, 88, 89`: complies; every stderr write goes through `visible()` on a tty.
- `cli_output.py:346`: exempt, docstring.
- `cli_output.py:357`: complies; every stdout write on a tty in human mode. `print` writes the text and the LF as two calls, so a reset lands at the end of the text call; an SGR split across calls is escaped, not kept.
- `cli_output.py:409`: complies; `json.dumps` escapes ESC.
- `cli_output.py:776`: complies; `!r` repr, then `err()`.
- `cli.py:1924, 1927`: exempt because an export file gets the bytes as captured, by design.
- `cli_daemonctl.py:153`: exempt, the user's own env var in a repr.

No rich `Console`, `click.echo`, or `sys.__stdout__` bypass exists (grep). The web UI renders device text as `textContent` only. No daemon log line carries device text.

## 98. A file or directory created with the default mode

`sweep.sh` (every creating `open`, `os.open`, `O_CREAT`, `makedirs`, `mkdir`, `mkstemp`, `sqlite3.connect`, `AtomicOut`, `make_private_dirs`): 43 sites.

### Violations

- **V98-1** `cli_daemonctl.py:71` (`_open_append`, POSIX branch).
  - Failure: the spawned daemon's stderr log (`mcuscoped-<host>-<port>.err`) is created by `open(path, "ab")`, mode `0666 & ~umask`. A data dir created before SEC-6 (0775) leaves it group/world readable.
  - Fix: `open(path, "ab", opener=dirs.private_opener)`.
- **V98-2** `cli_daemonctl.py:262` (`_replace_pid_record`).
  - Failure: the temp record is `open(tmp_path, "w")` at the default mode and replaces the record, so a record rewritten by `_record_own_daemon` loses the 0600 that `pidfile.create_record` gives it.
  - Fix: `opener=private_opener`, as `pidfile.py:304` does.

### Verdict list

- `_stdio.py:374, 375`: complies; private dirs and `private_opener`.
- `_stdio.py:406`, `cli_output.py:219`: exempt, `os.devnull`.
- `cli.py:1926`, `cli_client.py:361`: exempt because an export the user names keeps the replaced file's mode (`AtomicOut`, `cli_output.py:268`).
- `cli_daemonctl.py:62, 257`: exempt, docstring and comment.
- `cli_daemonctl.py:71`: violates, V98-1.
- `cli_daemonctl.py:89`: exempt, Windows branch.
- `cli_daemonctl.py:262`: violates, V98-2.
- `cli_output.py:260`: exempt (as `cli.py:1926`); `mkstemp` 0600, then the target's mode or the umask default for a new export.
- `config.py:614, 615, 618`: complies; `write_new_file` creates 0600, or the replaced file's mode on a new temp.
- `config.py:624`: complies.
- `dirs.py:29, 30, 32, 47`: complies; `make_private_dirs` creates each missing level 0700.
- `dirs.py:35`: exempt, the non-POSIX branch.
- `dirs.py:55`: complies; `private_opener`.
- `lockfile.py:135, 166, 167`: complies; 0700 dirs, lock 0600.
- `pidfile.py:64, 181, 304, 331`: complies.
- `serial_link.py:1111`: exempt, `_write_bytes(` matched `write_bytes(`.
- `server.py:1783, 1914, 1915, 3075, 3077`: complies; `mkstemp` 0600.
- `server.py:1928`: exempt, a zip member.
- `store.py:1030, 1035, 1036`: complies; parent 0700, DB pre-created 0600 so `-wal`/`-shm` inherit it.
- `store.py:2099`: complies; connects to a `mkstemp` file.
- `store.py:2675, 3391`: complies; `_check_path` runs first, so a deleted capture is not recreated at the default mode (microsecond residual).
- `update_check.py:215`: complies; private dirs, then `write_new_file`.

## 99. A throttle keyed finer than the attacker's allocation unit

`sweep.sh` (`_fails`, `_throttle_key`, the subscriber cap, every read of the client address): 21 sites.

### Verdict list

- `server.py:811`: complies; IPv4 per address, IPv6 per /64, IPv4-mapped folded to IPv4.
- `server.py:854, 856, 859, 863, 865, 871, 883, 887, 888, 889, 890, 891`: complies; the table is keyed by `_throttle_key` (eviction silence is V95-2).
- `server.py:916, 921`: complies; the key is taken before the lockout check.
- `server.py:935`: complies; a correct token clears that /64's record.
- `server.py:1325, 1425`: exempt, loopback checks, not tables.
- `store.py:290, 1797, 1798`: exempt because the subscriber cap is global, not per client, and only loopback or token-holding clients reach `/ws` (OP-1); an open bind without a token is warned at start.

Note, not a violation: every IPv6 link-local client shares `fe80::/64`, so one link-local client's wrong tokens lock out every link-local client on the segment (coarser, not finer).

## 100. A documented behaviour nothing executes

`extract.py` (every `mcu ...` example in `AI_GUIDE`, `README.md`, `host/README.md`, `docs/CLAUDE_SNIPPET.md`): 142 sites (ai-guide 73, README.md 43, host/README.md 13, CLAUDE_SNIPPET.md 13). Full list: `class-100/examples.txt`.

Not run: the sweep needs a sim daemon and the brief forbids one (question at the top).
What ran: `c58-guide-flags.py` over the three Markdown files (no daemon); `host/README.md` and `CLAUDE_SNIPPET.md` name no unknown flag. `README.md` names `--force`, `--help`, `--host`, `--ignore-capture-lock`, `--python`, `--reinstall`, which belong to `uv`, `mcuscoped` and click (class 58).

### Verdict list

- `ai-guide:72`: exempt, a prose fragment ("mcu is refused"), not a command.
- The other 141: not run. ai-guide lines 6, 25, 31, 33, 38, 54, 73 (2), 76, 77, 83, 92, 110-114, 119, 120, 122, 125, 128, 130, 133, 141, 145, 148, 149, 151, 156-158, 160, 161, 167, 177, 182, 191-194, 210, 213, 215, 216, 231-233, 235, 236, 238, 241, 247, 251, 257, 261, 262, 265, 269-271, 274-276, 278, 279, 288, 291, 292, 315, 318, 323; README.md lines 54 (2), 72 (2), 73 (2), 82, 92, 131, 156, 167, 170-173, 197-202, 217-222, 234, 236-239, 250, 251, 260-264, 271, 333, 334, 338; host/README.md lines 13 (2), 18, 63-66, 68-71, 77, 79; CLAUDE_SNIPPET.md lines 13, 14, 16, 17, 18 (2), 19 (3), 21, 22, 30, 32.

## 101. An injection guard judged at one delimiter

`sweep.sh` (`_csv_cell`, `csvField`, CSV writers and media types in host and web UI): 17 sites.

### Verdict list

- `server.py:2182, 2377, 3802`: exempt, media types and the format table.
- `server.py:3735`: complies; `_FORMULA_START` guards the start and every position after `;`, TAB, CR or LF, then RFC-4180 quotes.
- `server.py:3789, 3794, 3817, 3850, 3851, 3871, 4005`: complies; every device- or user-derived cell goes through `_csv_cell`; the rest are `_fmt_num` numbers.
- `server.py:3793`: complies; `dir` is the daemon's three-value vocabulary (`formula_guard=False`).
- `can.js:601, 605, 622`: exempt because ruled (triage SEC-3): `csvField` sees only `_ALIAS_RE` names and hex.
- `can.js:627, 738`: exempt, blob type and export list.

Note: the `can.js:601` comment ("Mirror of server.py _csv_cell, rule for rule") and `csv_cell_cases.json`'s role are no longer true: the fixture has no `;`/TAB-position case, so the two engines differ unpinned. Either reword the comment or port `_FORMULA_START` and add the cases.

## 102. A pointer gesture ended only by `pointerup`

`sweep.sh`: 9 lines (4 sites, 5 context lines it prints to rule them against).

### Verdict list

- `app.js:104, 105`: complies; ended by `pointerup`, `pointercancel` and `lostpointercapture` (`:129`), and `.resizer` sets `touch-action: none` (`style.css:147`).
- `app.js:156, 157`: complies; the same three (`:178`), `.hdivider` `touch-action: none` (`style.css:249`).
- `app.js:127, 129, 178`, `style.css:147, 249`: complies, the handlers and rules named above.

No `mousedown` or `touchstart` drag exists outside vendored uPlot.

## 103. Another process on the capture, unannounced

`sweep.sh` (busy timeouts, retries, checkpoints, `sqlite3.connect`, WAL and lock reads in `store.py`, `server.py`, `daemon.py`): 68 sites.

### Violation

- **V103-1** `store.py:3757-3784` (`sweep_tick`) with the loop-connection writes it drives (`_delete_lines`, `_reclaim_pages`).
  - Failure: the sweeps write on the writer connection with its 5 ms busy timeout. Another process's write lock fails them as `retention sweep failed: database is locked` at error level, on every tick that needs a write; the failure is not joined to the lock episode (`db_locked_since`, `/status`, the sys row at clear), so on a quiet board the only trace is repeated error logs. Partial deletes are V94-1 and V94-2.
  - Fix: treat `_is_busy` in `sweep_tick` as the lock episode: set `db_locked_since` if unset, skip the sweep without an error, and let the clearing sys row cover it.

### Verdict list

- `store.py:220, 223, 224, 228, 229, 230, 303, 747, 754, 836, 878, 893, 896, 931, 936, 986, 1032, 1104, 1107, 3563, 3788, 3816`: exempt, constants, comments and docstrings.
- `store.py:419, 420, 424`: complies; `_is_busy` reads SQLITE_BUSY and SQLITE_LOCKED.
- `store.py:678, 686, 707, 2693, 2694, 2724`: exempt, the regex budget, not the database.
- `store.py:748, 1379, 1462, 1472, 1476, 1482, 1487, 1488, 1498, 1501, 1502`: complies; the writer keeps its batch, backs off on the loop by `await asyncio.sleep`, logs once, shows `db_locked_since`, and writes one sys row when the lock clears. A stop while locked loses the clear row; the next start's RES-7 notice records the gap.
- `store.py:903, 922`: complies; the TRUNCATE checkpoint is bounded by `_WAL_EMPTY_TRIES`, then the WAL is moved aside and the reason announced.
- `store.py:988`: complies; 5 ms on the writer.
- `store.py:1036`: complies; the start-time connection keeps the 5 s default before serving, and a held lock fails startup (aside: as a lifespan traceback, a class 9 candidate).
- `store.py:1043`: complies (class 92).
- `store.py:1109`: complies; `journal_size_limit`.
- `store.py:1205`: complies; the writer join at stop is bounded at 5 s.
- `store.py:2099, 2675, 3391`: complies; off the loop (worker threads), default 5 s bound.
- `store.py:3796, 3798, 3801`: complies; a PASSIVE checkpoint never waits, and WAL growth past the limit is announced once per episode. Aside: `os.path.getsize` runs on the loop (class 1 territory).
- `store.py:3826`: complies; a reporting size read.
- `server.py:498, 3061`: exempt, comments.
- `server.py:1289`: complies; `/status` `db_locked_since`.
- `server.py:2453, 2595, 2627, 2765, 3283`: exempt, WS keepalive, regex and live-wait timeouts.
- `server.py:2986, 3176`: exempt, the export pool's own cap.
- `server.py:3100, 3103`: exempt, the export abandon path.

## 104. One time window resolved in two places from two anchors

`grep -n "floor_ts\|ceil_ts\|since_ts\|last_ms\|until_ts"` over `server.py`, `store.py`, `cli.py`: 201 sites.

### Violations

- **V104-1** `cli.py:795-831` (`_absolute_window`), used at `:1029` (`lines`), `:2045` (`log export`), `:2241` (`can dump`).
  - Failure: `--last-ms` becomes a `since_ts` floor counted from the daemon's anchor, with no ceiling; SPEC 3.4 (`SPEC.md:1008`) gives a `last_ms` window a ceiling of anchor plus the 10 s slack.
    After a clock step back, `mcu lines --last-ms N` and `mcu log export --last-ms N` include rows stamped ahead of the anchor that `mcu assert --last-ms N` (daemon-resolved, `cli.py:1611`) excludes: FD-STORE-3's shape, moved to the CLI.
  - Fix: return the ceiling with the floor (`until_ts = anchor + WINDOW_TS_SLACK_S`, `min` with `--to`), or let the daemon take a resolved floor and ceiling.
- **V104-2** `store.py:2371-2372` (`_window_terms`), latent.
  - Failure: a caller passing `floor_ts` without `ceil_ts` gets a ceiling re-derived from `id_to` by `_window_anchor`, a second anchor. Every current caller passes both (`_resolve_window` sets them together), so nothing reaches it today.
  - Fix: `assert ceil_ts is not None` when `floor_ts` is given and `last_ms` is not, or make them one argument.

### Verdict list

server.py (55)
- `329, 2065, 2066, 2067, 2097, 2098, 2099, 2145, 2146, 2147, 2262, 2283, 2285, 2286`: exempt, request parameters.
- `2074, 2110, 2156, 2306, 3688, 3692, 3695, 3696`: complies; `_check_window` refuses only an inverted pair, no resolution.
- `2080, 2117, 2173, 2322, 3423, 3424, 3426, 3427, 3434, 3435`: complies; each route resolves once through `_resolve_window`, and `/assert` hands the store the same `floor_ts`/`ceil_ts` pair.
- `2274`: complies; `/plot/series` passes `last_ms` to the store, which anchors floor and ceiling once in `_window_terms`.
- `3352, 3354`: complies; a live verdict refuses `last_ms`.
- `3578, 3585, 3586, 3590, 3594, 3596, 3599`: exempt, docstring and signature.
- `3607, 3608, 3627, 3628`: complies; one anchor, floor and ceiling (`ceil_ts`) both derived from it; the freeze is applied after and does not move it.
- `3609, 3610, 3615, 3616, 3617, 3621`: complies; file-name bounds and the `until_ts` id ceiling, each from its own explicit bound.
- `3704, 3705, 3724`: exempt, export file name.

store.py (91)
- `723, 1517, 1544, 2271, 2312, 2313, 2314, 2315, 2316, 2327, 2331, 2333, 2336, 2389, 2400, 2405, 2410, 2415, 2710, 2737`: exempt, messages, docstrings and parameters.
- `2269, 2283`: complies; `_window_floor` uses `_window_anchor`.
- `2368, 2373, 2374, 2375, 2377, 2381`: complies; with `last_ms`, floor and ceiling come from one `_window_anchor` call.
- `2371`: violates, V104-2.
- `2382, 2383, 2385, 2387, 2388, 2393, 2396`: complies; explicit `since_ts`/`until_ts` bounds and their paired id bounds.
- `2399, 2429, 2433, 2437, 2460`: complies; id ceiling and floor helpers, each from the single ts it is given.
- `2472-2476, 2490, 2491, 2535-2537, 2553, 2564, 2565, 2784-2788, 2800, 2801, 3036, 3065, 3140, 3143-3145, 3152, 3153, 3160, 3163-3165, 3177, 3195, 3200-3203, 3215, 3216, 3240, 3244-3247, 3278, 3279`: complies; plumbing that hands the resolved window to `_window_terms` unchanged. `2553` keeps `id_to` whenever a time window is in play, because `id_to` anchors it.
- `2655, 2657, 2716`: complies; the id ceiling for `until_ts` and its offload rule.

cli.py (55)
- `647, 648, 661, 662, 663, 664`: complies; plumbing of explicit bounds.
- `759, 761, 762, 765, 768, 772`: complies; `_pin_ceiling` derives the id ceiling once from `until_ts`.
- `795, 797, 799, 803, 808, 809, 827, 828, 831`: violates, V104-1.
- `835`: exempt, comment.
- `845, 846, 847, 848, 853`: complies; `--from`/`--to` parsed once.
- `1003, 1556, 2007, 2204, 2586`: exempt, option declarations.
- `1028, 1031, 2044, 2049, 2062, 2237, 2238, 2243, 2244, 2245, 2246`: complies; plumbing.
- `1029, 2045, 2241`: violates, V104-1 (the call sites).
- `1611, 1612, 2621, 2623, 2624, 2625, 2626, 2627, 2628`: complies; `last_ms` is sent to the daemon, which resolves it once.

## Scratch

Kept, for later rounds: `~/tt-data/mcuscope-tools/sweeps/class-92/` to `class-104/` (`sweep.sh`, `out.txt`; `class-100/extract.py`, `examples.txt`). Nothing else was created.


## Class 100. A documented behaviour nothing executes

142 examples (`~/tt-data/mcuscope-tools/sweeps/class-100/examples.txt`), 3 violate, 14 exempt, 125 comply.
Run by `~/tt-data/mcuscope-tools/sweeps/class-100/run.py` against `mcuscoped --sim` on 19260 (own TOML, `db_path`, `MCUSCOPE_*_DIR`, `MCUSCOPE_URL`), plus a second daemon on 19261 for the start/stop/restart and exit-3 examples, and two `mcu-sim` listeners (9900 and an ephemeral port) for the two-port examples.
Besides the 142 examples the runner asserts 72 further behaviour claims from the guide (exit codes, `-p` rules, truncation notes, `--json` error kinds, paging, `--csv` refusals, purge refusals, sessions, bundle contents); all 72 hold.
Per-command actuals: `~/tt-data/mcuscope-2026-10-05/class100/out/actuals.txt`, verdicts `results.json`.
Ports 19260, 19261 and 9900 free afterwards; 8558 never touched.

### Violations

- `ai-guide:83` (`mcu status`): violates, V100-1.
  - Command: `mcu status` after `POST /ports/board/disconnect`.
  - Expected (guide HEALTH): "each port shows its state (connected / disconnected (REASON) / DEGRADED ...)", `manual` being "closed by POST /ports/<alias>/disconnect".
  - Actual: exit 0, `board  socket://127.0.0.1:41111  @115200  held (disconnected on request)  rx=187 tx=5`; `--json` does carry `disconnect_reason: "manual"`.
  - The `held (...)` state text is not in the guide.
- `ai-guide:251` (`mcu plot export ...`): violates, V100-2.
  - Commands: `mcu plot export --last-ms 5000 --names ramp --changes -o p.csv` and `... --deadband ramp=0.05 -o p.csv`.
  - Expected: the guide lists `--decode`, `--changes` and `--deadband` as independent options (for `lines`, `--changes` is stated to imply `--decode`).
  - Actual: both exit 1, `error: changes requires decode`; `--deadband` needs `--changes` (`--help`: "With --decode", "With --changes").
  - With `--decode --changes --deadband ramp=0.05` it exits 0.
- `ai-guide:261` (`mcu can tx 1A3 DEADBEEF [--ext] [--rtr 4] [--bus 2] [--retry-ms 500]`): violates, V100-3.
  - Command: `mcu can tx 1A3 DEADBEEF --rtr 4`.
  - Expected: the synopsis puts DATA and `--rtr 4` together.
  - Actual: exit 1, `Invalid value for --rtr: --rtr and DATA are mutually exclusive`; `mcu can tx 1A3 --rtr 4` exits 0.

### Exempt (14)

- `ai-guide:72`: extractor false positive, prose "mcu is refused".
- `ai-guide:73` (2 examples): the version-skew refusal needs a daemon older than the client.
- `ai-guide:92`, `ai-guide:288`: prose pointers to `daemon restart` and `wait --repeat-ms`, both run under their own lines (318, 145).
- `ai-guide:119`: needs a USB device with serial 0672FF3; the syntax was run (accepted, `no_device`, `--serial` with a device refused).
- `ai-guide:157`: PowerShell syntax; the bash form on 156 ran.
- `ai-guide:194`: the `!e ...` notice texts need firmware to emit them; the command ran.
- `README.md:171`, `host/README.md:65`: `/dev/ttyACM0` is the bench board, never attached from a sweep.
- `README.md:172`, `host/README.md:66`: Windows COM port name.
- `README.md:222`, `README.md:251`: `selftest` is a firmware command; the sim's `ERR badcmd` gave the documented `send_failed` / FAILED verdict, exit 1.

### Complies (125)

- `ai-guide`: lines 6, 25, 31, 33, 38, 54, 76, 77, 110, 111, 112, 113, 114, 120, 122, 125, 128, 130, 133, 141, 145, 148, 149, 151, 156, 158, 160, 161, 167, 177, 182, 191, 192, 193, 210, 213, 215, 216, 231, 232, 233, 235, 236, 238, 241, 247, 257, 262, 265, 269, 270, 271, 274, 275, 276, 278, 279, 291, 292, 315, 318, 323
- `README.md`: lines 54, 54, 72, 72, 73, 73, 82, 92, 131, 156, 167, 170, 173, 197, 198, 199, 200, 201, 202, 217, 218, 219, 220, 221, 234, 236, 237, 238, 239, 250, 260, 261, 262, 263, 264, 271, 333, 334, 338
- `host/README.md`: lines 13, 13, 18, 63, 64, 68, 69, 70, 71, 77, 79
- `docs/CLAUDE_SNIPPET.md`: lines 13, 14, 16, 17, 18, 18, 19, 19, 19, 21, 22, 30, 32

### Not covered by any run

- `mcu daemon start --config PATH` binding that file's `[server]` with no `MCUSCOPE_URL`: a failure would poll the default 8558, so it was run only with `MCUSCOPE_URL` set.
- LAN bind and `--token`, `--open` (browser), the Tier 1 and 2 firmware `printf` formats (`!p`, `!m @tick`), and the `ERR 6 busy` retry: need a LAN, a browser or a firmware.
- The `!e` notice texts and the `incomplete` (shed lines) verdict.

### Observations (not violations)

- The sim emits `ERR 3 timeout i2c 0x48 read gave no ack, retrying` as a debug line now and then, so `assert --last-ms 10000 --forbid ERR` (ai-guide:215) passes or fails by timing against the sim; it passed in the run.
- `mcu sysrq b` leaves a bare `b` unterminated on the line; the next monitor command on that port then got no response (`wait --send` ended `send_failed`).
  The guide gives the follow-with-`mcu send ""` advice only under `--eol none`, not under `sysrq`.
