# Fix batch: integration (fix-diff leg, 2026-09-24)

HEAD: `b994076c88c875ad43c30789cda6039d147b7abe` (uncommitted work from every batch on top).

Scratch: `~/tt-data/mcuscope-2026-09-24/fixbatch-integration/`.

- `copy/host/` is the private mutant copy; `tests/test_zz_probe.py` there asserts that `mcuscope` imports from the copy, both in-process and in a child.
- `mut.py` is the revert harness, and `mut.log` holds its results (21 mutants, all KILLED). `mut_one.log` holds the finding 9 test run on its own.
- `runs.log` holds the per-file runs; `plan_probe.py` is the query-plan check.
- `second-opinion-fable.md` is the fable-low second opinion.

## Question for the owner (item 4)

Recommended: the index-build note ends `Ctrl-C leaves it building (pid N)`, not the store report's `mcu daemon stop abandons it`.

- `mcu daemon stop` cannot stop a daemon that is still building.
  - `/status` does not answer during the build, and the record's pid is alive.
  - So `_stop_daemon` (`cli.py`, "no usable /status ... left its record in place") exits 1 and signals nothing.
- Naming it would be class 58 (in-product help naming a command that does not do what it says).
- The pid lets the user kill the build by hand. Killing it is safe: SQLite rolls the index back, and the next start builds it again.
- Implemented with the recommended wording. Swapping the text is a one-line change in `cli.py` plus the assertion in `test_cli_start_index_build.py:91`.

## Per item

### 1. `[port]` for a detached board's history (CLI finding 3)
- `store.py:2003` `stored_ports(conn=None)`: the recursive skip walk that `_scan_plot_summary` already used, moved out and called from there (`:2384`).
  - It starts at `port > ''`, so the daemon's own rows (port `""`, SPEC 3.5) are not counted as a board.
    - Without that, every capture counted 2 ports and got the column. `test_a_single_board_capture_has_no_port_column` caught this on the first run.
  - Plan: `SEARCH lines USING COVERING INDEX idx_lines_port_id (port>?)` for the seed and for each step (`plan_probe.py`). The test pins exactly two `lines` steps, both on that index.
- `server.py:1144` `GET /ports` gains `stored`.
- `server.py:3376` `_several_ports(request)`: returns true when more than 1 port is attached or more than 1 port has stored rows.
- `server.py:3370` `_text_lines(rows, show_port=False)`.
- `server.py:1906`: `/lines/export?format=text` passes `port is None and _several_ports(request)`.
- `server.py:1631`: the bundle's `lines.txt` uses the same rule, since SPEC:870 defines it as the `/lines/export` text rendering. The second opinion agrees (A).
- `cli.py:869` `_port_column`:
  - the stream branch calls `_stream_port_column` (`:884`), which reads `ports` and `stored` from the one `/ports` probe, using `.get` and a type check on each;
  - the finished-result branch now ignores port `""` (`:881`). This is the same flaw in the CLI: a `sys` row plus one board's rows used to turn the column on.
- `cli.py:1921` `log export`:
  - it streams whenever the daemon reports `stored`, since that daemon renders the column itself;
  - it pages only for a daemon without `stored` when more than one port is attached. That daemon's text has no column, and this is the path the CLI used before.
  - This deviates from "drop the paged-path gate": the gate is dropped for every 0.5.0 daemon and kept as the pre-0.5.0 compatibility branch. The comment says to delete it when that support goes.
  - Second opinion: A (keep the branch), because dropping it silently loses the column against an older daemon.
- Tests, `tests/test_port_column_stored.py` (11):
  - `:46` checks the plan and that `""` is excluded;
  - `:78` checks the rule's two operands separately;
  - `:86`, on a live stack with `b2` stored but detached:
    - `/ports` `stored`;
    - `[b2]` in the text export;
    - no column under `port=b2`;
    - `[b2]` in the bundle's `lines.txt`;
  - `:104` is the positive control: a single-board capture has no column, from the daemon or from `mcu log export`;
  - `:119` runs the real `mcu` child: `[b2]` appears in `log export` stdout, in the `-o` file bytes, and in the `tail -n 500 -f` backfill;
  - `:162` covers the `""` port in a finished result, with a two-board positive control;
  - `:178` is the stream rule;
  - `:189` is class 46: no `stored`, a malformed `stored`, and a non-object body;
  - `:195`: a multi-board export against a daemon reporting `stored` streams `/lines/export` and never pages.
- `tests/test_cli_read_scope.py:67`: the docstring now says the paged path is for a daemon older than `stored`. The test is otherwise unchanged and pins that branch.
- Revert: 10 mutants, all KILLED:
  - `""` included;
  - each operand of the rule;
  - the `port=` filter ignored;
  - the bundle without the column;
  - `/ports` without `stored`;
  - the CLI ignoring `stored`;
  - the old gate kept;
  - the gate dropped for an old daemon;
  - the CLI counting `""`.

### 2. `ppid` in `/status`, `serial_number` in the port rows (CLI findings 10, 1, 12)
- `server.py:1063`: `"ppid": os.getppid()`.
- `serial_link.py:1293`: `"serial_number": self.serial_number`, null for a device attach. It reaches both `GET /ports` and `/status` `ports`, which share `status()`.
- Tests, `tests/test_status_ppid_serial.py`:
  - `:22`: `pid`/`ppid` in `/status`; `serial_number` null for a device attach and the SN for a serial attach.
  - `:32`: on the live stack, re-attaching the same `--serial` prints no `note:` (before this, it printed `was attached to ZZ99; it now names serial ZZ99`). Positive control: retargeting to a device prints `was attached to serial ZZ99`.
  - `:55`, end to end:
    - a real launcher shim (a shebang script that runs the interpreter as its child) starts a real daemon;
    - the daemon uses a throwaway config with its own `db_path` under `tmp_path`;
    - only `cli_daemonctl.sys.platform` reads `win32`;
    - `daemon start` exits 0 with the shim's pid; `/status` `ppid` equals it and `pid` differs;
    - `daemon stop` prints `stopped mcuscoped (pid <shim>)`.
    - POSIX-only, because the shim is a shebang script. No daemon was left behind (checked with `ps` afterwards).
- Revert: 2 mutants KILLED (no `ppid`, no `serial_number`).

### 3. Store finding 9: the export copy stops on an abandon between two statements
- `server.py:2793` `_ExportJob.on_open`: `conn.set_progress_handler(lambda: self._abandoned, 1000)`, after the existing refuse-if-abandoned check.
- `_conn`, `abandon()`'s `interrupt()` and its `suppress(sqlite3.ProgrammingError)` (the server batch's finding 1 fix) are deleted, and `server.py:2837` gets a comment instead.
  - The handler stops every statement that `interrupt()` could, at the same VDBE points, and it never touches a connection that may already be closed.
  - Second opinion: B (delete). It checked the `finally`'s rollback and DETACH on an abandoned copy: any raise there replaces one abandon-path error with another, and `job.run` still removes the files.
- Composition with the server batch's finding 1:
  - the closed-connection case is now impossible rather than suppressed;
  - `test_server_fixdiff_exports.py` still passes (7), including `test_a_bundle_cancelled_after_its_copy_logs_no_failure`.
- `tests/test_server_exports.py`:
  - `:288` is new, `test_an_abandon_between_open_and_the_first_statement_stops_the_copy`. It builds on 3000 real rows, abandons inside `on_open` before any SQL, and asserts `OperationalError interrupted`, no copy count and no temp files.
  - `:186` `_ProgressHook`: the `_SlowCopy` double installed its own progress handler, which replaced the job's (a connection holds one). It now takes the job's handler and calls it from its crawl, so the double is no gentler than production (class 27).
- Revert: removing the handler fails `test_a_cancelled_export_interrupts_its_copy` (the first failure under `-x`). Run alone, it also fails the new `:288` test (`mut_one.log`).

### 4. Store finding 2: `daemon start` waits out an index build
- `cli_daemonctl.py:88` `_stderr_lines` (now shared by `_stderr_tail`); `:104` the two literals, copied; `:108` `_index_build(err_path, start)` returns the names, and whether a `built index` line followed.
  - The notice is matched on any line from `err_start`, never only the last one.
- `cli.py:2663` readiness loop. At the deadline, with the child alive:
  - `building index` and no `built index`: print the note once (stderr only, so `--json` stdout stays pure) and keep polling;
  - `built index` seen: restart the `--timeout` countdown, once;
  - no notice, or a second expiry after the restart: `_abandon_daemon` as before.
- Differs from the spec in one case: a build that finished inside the first timeout also gets the one fresh countdown. The spec only says "once the `built index` line appears". The extra time is bounded.
- Tests, `tests/test_cli_start_index_build.py`, on a fake clock:
  - `:26` asserts the literals equal `store.INDEX_BUILD_NOTICE`/`INDEX_BUILT_NOTICE`;
  - `:84` is the build notice past the deadline, with another warning after it: not terminated, the note printed exactly once, and `--json` stdout parses;
  - `:97` is the positive control: the same silence without the notice is terminated at 5 s;
  - `:107`: after the build ends at 40 s, a daemon that never answers is stopped at 45 s;
  - `:124` is a build that finished inside the timeout.
- Revert: 7 mutants KILLED:
  - no wait;
  - no restart;
  - an unbounded restart (a pytest-timeout);
  - the note printed every poll;
  - the names not parsed;
  - `built` ignored;
  - `_stderr_tail` reading from 0.

### 5. Store batch's external test edits
- Applied as specified:
  - `test_regressions.py:158`;
  - `test_prerelease_daemon_core_store.py:36,38`;
  - `test_plot_export_since_id.py:38`.
- All pass (83, 3 and 19).
- The id-reuse test fails against the seed that ignores sessions (`self._next_id = self._max_id_sql(self._conn) + 1`): KILLED.

### 6. `AI_GUIDE`
- PITFALLS (`cli.py:2856-2859`): `[port]` "when more than one board is attached or has stored rows ... A detached board's history stays readable with -p."
- `daemon start` (`cli.py:3064-3066`): "one building an older capture's indexes (a one-time stderr note) is waited for, then given a fresh --timeout".
- `test_cli_contract.py` passes (27).

## Verification
- Each file was run alone in the shared tree (`runs.log`), 21 files, all green:
  - the three new files;
  - `test_server_exports`, `test_server_fixdiff_exports`, `test_cli_read_scope`;
  - `test_regressions`, `test_prerelease_daemon_core_store`, `test_plot_export_since_id`;
  - `test_cli_contract`, `test_cli` (167), `test_server_scope`;
  - `test_session_bundle`, `test_cli_daemon_stop_scope`, `test_cli_fixdiff_attach`;
  - `test_store_plot_reads`, `test_store_fixdiff_reads`, `test_e2e`;
  - `test_fixdiff2_cli`, `test_cli_ux`, `test_review_r2_cli`.
- `uv run python -m ruff check .` in `host/`: clean.
- 21 mutants in the copy, all KILLED (`mut.log`).

## SPEC wording (not edited)
- 3.4 `/status` JSON (SPEC:643): after `"pid": n,` add `"ppid": n,`. After SPEC:689 add:
  - "`ppid` is its parent: on Windows a venv launcher shim spawns the interpreter as a child, so the pid `mcu daemon start` spawned and recorded is the serving daemon's `ppid`."
- 3.4 port rows (SPEC:651): add `"serial_number": "0672FF3" | null,`. After SPEC:667 add:
  - "`serial_number` is the serial number a port was attached by, null for a device attach."
- 3.4 `GET /ports` (after SPEC:701):
  - "The list answers `{"ports": [...], "stored": ["b2", "board"]}`: `stored` names every port with stored rows, attached or not (sorted, the daemon's own port `""` excluded), which a `port=` filter still accepts."
- 3.4 `/lines/export` (SPEC:774): change the `text` clause to:
  - "`text` is the rendering `mcu log export` writes (`<hh:mm:ss.mmm> <chan>| <raw>`, and `<hh:mm:ss.mmm> [<port>] <chan>| <raw>` when no `port=` is given and more than one port is attached or has stored rows; each line boundary inside `raw` shown as `\xNN`/`\uNNNN`)".
- SPEC:870: no change needed; `lines.txt` stays "the `/lines/export` text rendering", now with the same rule.
- SPEC 4 (SPEC:1129):
  - "A read without `-p` spans every port; its text rows then carry a `[port]` column after the time whenever more than one board can appear: a finished result is judged on its rows, and a stream or `log export` on the ports attached plus the ports with stored rows (`GET /ports` `stored`), the rule the daemon's text export applies. A detached board's history stays readable with `-p`."
- SPEC 4 `daemon` row (SPEC:1179), after "whose lines from this start are shown when the start fails;":
  - "a start whose daemon's stderr announces an index build (`building index <names>`, 3.2) is not stopped at `--timeout`: it prints `mcuscoped is building index <names> on an older capture (one time); waiting. Ctrl-C leaves it building (pid N)` once on stderr, waits for `built index`, then allows one more `--timeout`;"
- SPEC 3.2 (SPEC:425): the store batch's deferred sentence is now true, so add "`mcu daemon start` waits for the build rather than timing out."
- SPEC 3.4 `/sessions/{ref}/export`: none needed. The progress handler is internal.

## ARCHITECTURE wording (not edited)
- Under `store.py`: "`stored_ports()` skips along `idx_lines_port_id` (one seek per port, the daemon's port `""` excluded); `/ports` `stored` and the text export's port-column rule read it on the loop, like `has_port_rows`."
- Under `server.py`, beside the export pool line (ARCHITECTURE.md:35): "An abandoned build is stopped by a progress handler on the copy's connection (`_ExportJob.on_open`), not `interrupt()`, which is lost between two statements; `checkpoint()` stops the bundle's later members."
- Under `cli_daemonctl.py`: "`daemon start` keys its readiness wait on the store's `building index`/`built index` notices in the daemon's stderr file, duplicated as literals (a test holds them equal) because importing `store.py` is heavy."

## Proposed CHANGELOG lines
- Fixed: text exports and `mcu tail -f`, `wait` and `log export` without `-p` carry `[port]` when a second board's rows are stored, even after it is detached, not only while two boards are attached.
  - This covers the daemon's `/lines/export?format=text`, the web UI's all-ports pane export and a session bundle's `lines.txt`.
- Fixed: a single-board capture no longer shows `[port]` in `mcu lines`/`tail` because of the daemon's own rows.
- `GET /ports` reports `stored`, the ports with stored rows; `/status` reports `ppid`; port rows report `serial_number`.
- Fixed (Windows): `mcu daemon start` from a venv reports success, and `daemon stop` can signal the launcher it recorded (needs a daemon reporting `ppid`).
- Fixed: `mcu attach --serial` re-attaching the same serial no longer prints a retarget note.
- Fixed: a session export or bundle abandoned before its copy's first statement stops instead of running to the end.
- `mcu daemon start` waits for the one-time index build of an older capture instead of stopping the daemon at `--timeout` (and starting the build over next time).

## Not done
- The latent issue in the second opinion: a `tail -f`/`wait` that began with the column off never shows it for a board attached mid-stream. It was not in this brief.
- No live `mcu daemon start` against a large older capture (a real multi-second index build). The wait is driven only on a fake clock and a fake child.
- SPEC, ARCHITECTURE and CHANGELOG (wording above).

## Needs Windows
- The real venv shim: `mcu daemon start` then `mcu daemon stop` from a uv or venv install. Expect exit 0, `started mcuscoped (pid <shim>)`, then `stopped mcuscoped (pid <shim>)`.
  - `test_status_ppid_serial.py:55` drives the same path on POSIX with a shebang shim, and is skipped on win32.
- The index-build wait with the Windows `.err` handle (`_open_append`): the CLI reads the file while the daemon holds it open for append.

## Doubts
- The item 4 note text: see the question at the top.
- `stored_ports()` runs on the loop's connection, like `has_port_rows`: k+1 index seeks for k ports. It is cheap for a bench's handful of boards; a capture with thousands of distinct port names would make `/ports` and each text export pay that on the loop.
- The rule is over-inclusive by design. A capture that once held a second board shows the column on single-board exports (and sessions) until retention or purge removes that board's rows.
- The column rule is judged when the request arrives (and when the bundle's entries are built), not re-judged as rows stream. That matches the CLI's stream judgement.
- The paged fallback for a pre-0.5.0 daemon remains test-pinned, dead weight once that support is dropped.
