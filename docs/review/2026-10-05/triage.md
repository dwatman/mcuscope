# Triage, 2026-10-05 round

Reports: `fuzz.md` (5), `security.md` (7), `resilience.md` (10), `agentux.md` (10), `modules.md` (4); 36 findings. `soak.md` not yet in.
Code at HEAD 5eaaeeb is identical to the fuzz leg's base 0e1b7e9 (`git diff --stat` touches docs only).
Probes: `~/tt-data/mcuscope-2026-10-05/triage/` (isolated `--sim` daemon on 18720, a corrupt-capture daemon on 18721, both stopped by PID; `fp/` footprint rebuild; `lock/` symlink probe).

## Verdicts

Confirmed 36, refuted 0, downgraded 0.
Driven again here: FUZZ-1, FUZZ-3, FUZZ-4, SEC-4, SEC-6, RES-1, RES-8, MODULES-1, AGENTUX-2, 4, 6, 7, 8, 9, 10.
Confirmed by reading the cited lines only: the rest. Of those, four have a mechanism read but a trigger nobody drove: SEC-7 (an IPv6 /64 sweep), AGENTUX-5 (`dropped > 0`), MODULES-2 (the Windows half), MODULES-4 (a touch `pointercancel`).
Refuted sub-claim: AGENTUX-7's "`daemon unreachable` does not suggest `mcu daemon start`". `start_hint` (`cli_client.py:72`) adds it for the default URL and leaves it off on purpose for a custom `--url`, which is what the leg used.

| Finding | Re-drive or reading at HEAD |
|---|---|
| FUZZ-1 | `triage/fuzz1.py`: `!can 1 - 100 00\r` gives token `00\r`, so there is no frame; the folded form decodes id 0x100. The same split holds for `!p` and `!pd`. `serial_link.py:819` strips one CR, while `store.py:1367` folds after classification. |
| FUZZ-2 | `monitor.c:1038` masks the id. SPEC 2.5 (`SPEC.md:227`) says the id is not range-checked, and INTEGRATION.md:306 says it is masked: SPEC and code disagree. |
| FUZZ-3 | Rebuilt `src` and `src_hooks` (M0+ -Os, app printf). Core goes 4997/1112 to 4481/968, plot goes 7205/1112 to 7361/1132, exactly as reported. |
| FUZZ-4 | The same build gives core 4997 B against the stated 4.6 KB. The `SPEC.md:1288` and `INTEGRATION.md:38` tables are stale. |
| FUZZ-5 | `serial_link.py:819` decodes with `ascii`/`replace`, and no counter is incremented. SPEC 2.1 and 2.2 do not mention the replacement. |
| SEC-1 | `server.py:781`, `:875` and `:1303` exempt loopback, and SPEC 3.1 says so by design. |
| SEC-2 | `store.py:723`: `_plot_summary` has no cap on distinct names, and `/plot/channels` (`server.py:2071`) lists them all. |
| SEC-3 | `_csv_cell` (`server.py:3576`) guards only the first character. It is the one helper behind all 8 CSV call sites. |
| SEC-4 | A 2 MB marker text is answered by a 2,000,069-byte 422 (`triage/sec4.py`). There is no body limit anywhere in `server.py`. |
| SEC-5 | `visible()` (`cli_output.py:49`) keeps SGR sequences and appends no reset. |
| SEC-6 | Triage data dir 775; `capture.db`, `-wal`, `-shm` and `.lock` 644. `lockfile.py:124` creates the lock with `0o644`. |
| SEC-7 | `_fails` (`server.py:812`) is keyed by the full client address. |
| RES-1 | `triage/res1.py`: an external `BEGIN IMMEDIATE` held 6 s stalls `/status` for 6.03 s. The writer is `sqlite3.connect` with the default 5 s timeout, retried row by row. |
| RES-2 | No `st_ino`/`st_dev` anywhere in the package. |
| RES-3 | `_drop_rx_line` logs per line, and its sys row is written through the failing store. `clear()` at `:978` is silent. |
| RES-4 | `_window_floor` (`store.py:1916`) sets a floor and no ceiling. |
| RES-5 | `_OutFile` writes to the target path directly. The guard runs only on exceptions; no signal is handled. |
| RES-6 | `_sweep_retention_locked` returns a total that `sweep_tick` discards. There is no sys row and no counter; `lines_trimmed` covers only the size cap. |
| RES-7 | `_close_crashed_auto_session` logs only, at info level. |
| RES-8 | A random 8 KB `db_path` exits 3 with the traceback ending `sqlite3.DatabaseError: file is not a database` (`triage/corrupt/out.txt`). |
| RES-9 | No passive checkpoint is run and nothing is announced. `content_bytes` excludes the WAL by design. |
| RES-10 | `CaptureLock.acquire` never compares `fstat(fd)` with `stat(path)`. |
| MODULES-1 | `triage/lock`: a second `CaptureLock` acquired through a symlink to `real.db` succeeds, while the same spelling is refused (control). |
| MODULES-2 | `daemon.py:449` locks `resolve_db_path(config)`, which returns `:memory:` verbatim. |
| MODULES-3 | `applySideWidth` is called only at load and on drag or key input. The only `resize` listener is `terminal.js:936`. |
| MODULES-4 | Only `pointerup` handlers exist (`app.js:113`, `:163`), and no `touch-action` is set in `style.css`. |
| AGENTUX-1 | `_start_daemon` passes `--host`/`--port` from the URL, and `daemon.py:104-111` lets those override the config. |
| AGENTUX-2 | `mcu mark NOPORT1`, then `-p sim lines --chan marker`, finds 0. Without `-p` it finds 1. |
| AGENTUX-3 | `_port_column` (`cli.py:888`) follows SPEC 4 (`SPEC.md:1166`); the guide (`cli.py:2941`) states the stream rule. |
| AGENTUX-4 | `wait --send bogus --match . --json` exits 1 with `"status": "match"`. With `--match zzzz --timeout 3000` it waits the full 3.0 s after the ERR arrived in 7 ms. |
| AGENTUX-5 | `verdict()` (`server.py:3214`) changes the status on `dropped` only when `checked == 0`, and the CLI only warns. |
| AGENTUX-6 | `{"error": "error: no such port: nope"}` against `{"error": "No such command 'nonexistent'."}`. |
| AGENTUX-7 | `cmd ping --timeout 1` prints the bare word `timeout`. `assert --session 1 --timeout 100` names `timeout_ms`. `can dump -f` "skipping bad update" read at `cli.py:1099`. |
| AGENTUX-8 | `cmd ping` prints `monitor 1 sim`, with no OK. `-p nosuch status` and `-p nosuch session start q` both exit 0. The snippet's line 22, and guide lines 2949, 3048, 3099 and 3110, read as reported. |
| AGENTUX-9 | `send --json` gives `{"ok": true}`. A repeated `purge --id-from 1 --id-to 1 -y --json` gives `"dry_run": true`. `plot export --json` puts the CSV in a string, with values like `8.700000000000001`. |
| AGENTUX-10 | `session list --limit 0` prints "no sessions recorded". `session delete boot-test` prints "(0 lines)". `lines --names nosuchfield` shows nothing and gives no warning. EMPTY goes to stderr, PASS/FAIL to stdout (`cli.py:1563`). |

## Merged

- **UNTRUSTED-BYTES**, FUZZ-5 + SEC-5: device bytes reaching storage and the operator's terminal.
  - The decode choice (OP-5) decides what can reach `visible()`. Today ASCII replacement is why no C1 or bidi control arrives from a board (security leg, "Checked and fine").
  - The SEC-5 half is fix-now either way.
- **UNRECORDED-LOSS**, RES-3 + RES-6 + RES-7: data lost or deleted with no row in the capture saying so (CLAUDE.md "counted or announced"). Each half has its own fix.
- **CAPTURE-IDENTITY**, RES-2 + RES-10 + MODULES-1: the capture and its lock are judged by path spelling at start and never by file identity afterwards.
  - The fixes are one mechanism: `realpath` keying, a `(st_dev, st_ino)` compare at start, and a re-check on the sweep tick.
  - MODULES-2 sits in the same batch but has a different root (class 52).
- Related, not merged:
  - RES-1 and RES-9: another process holds the capture, by a write lock or a read pin. Different fixes, one batch.
  - AGENTUX-4 and AGENTUX-5: a verdict that reads success while the operation failed or has holes.
  - AGENTUX-7 and AGENTUX-8: `--session` with `--timeout`; the snippet line and the server message are one fix.
  - SEC-1 and SEC-6: both are about other local accounts. SEC-6 is fix-now whatever OP-1 decides.

## Owner picks

Each is one question for AskUserQuestion; the recommended option is listed first.

### OP-1 (SEC-1): who is inside the trust boundary on a machine with several accounts?

1. **Single-user machine, stated.**
   - SPEC 3.1 and both READMEs say: on a shared machine every local account has full daemon control, including device access through `POST /ports` and port squatting on 8558.
   - SEC-6's 0600/0700 modes ship regardless. The agent REST recipes stay token-free.
2. **Per-user secret.**
   - The daemon writes a random token to a 0600 file in the data dir at start. `mcu` reads it and sends it on loopback too; `--open` passes it in a URL fragment, which the UI moves to localStorage.
   - Every raw-REST caller (the guide's `python3 urllib` recipes, PlotJuggler is UDP so unaffected) must read the file.
   - Squatting is not solved, and the client would hand the token to a squatter.
3. **Peer-uid check.**
   - Refuse a loopback peer owned by another uid: on Linux, look up the peer inode in `/proc/net/tcp{,6}`; on Windows, `GetExtendedTcpTable` owning pid, then its token. This is platform code on both sides of the cross-platform mandate.
   - Nothing changes for REST users, and squatting is still not solved.

Recommendation: 1. The tool's model is one developer's bench machine. 2 and 3 each add a contract (a token file, or per-OS socket introspection) and still leave squatting open. 2 is the upgrade path if shared hosts become a use case.

### OP-2 (SEC-2): how is the number of distinct ad-hoc plot names bounded?

1. **Cap at ingest, per port.**
   - The decoder admits at most N distinct ad-hoc names per port (for example 1024). An overflow `!p` line is stored as a generic event, counted on `/status`, and announced once per episode with a sys row.
   - This bounds `_plot_summary`, PlotJuggler and `mcu plot list` alike. `/plot/channels` also gets `limit` plus `truncated`.
2. **Bound only the summary.**
   - Points are still stored. The summary keeps stats for the first N names per port and only counts the rest; `/plot/channels` reports `more: N`. The names past N are reachable by `/plot/series` only.
   - PlotJuggler is still fed every name.

Recommendation: 1. One bound at the source covers every consumer, the overflow stays in the capture as text, and SPEC 2.5 gains one sentence.

### OP-3 (FUZZ-2): what does the monitor do with a CAN id wider than its flags allow?

1. **Emit it unmasked, as SPEC 2.5 says.**
   - The host keeps it as a generic event with its existing `!can decode failure` sys row. INTEGRATION.md:306 and the `test_can_id_mask` expectation change.
   - A filter matches on the unmasked id.
2. **Drop and announce, like an out-of-range bus.**
   - The first drop after init emits `!e can id <hex> dropped`. SPEC 2.5 and INTEGRATION.md change.
   - This costs a few dozen bytes of flash.
3. **Keep the mask** and correct SPEC to say so. This is a silent alteration, which the no-silent-data rule forbids.

Recommendation: 1. SPEC is the contract, the code changed without it (0b5eed9), nothing is lost, and it costs no flash. The vendored monitors (charger-test, charger_control, relay_control) need a re-vendor either way.

### OP-4 (FUZZ-3): should the plot registry and the overflow notice link only when used?

1. **Hooks.**
   - `monitor_plot` and the first `event_end` install function pointers that `monitor_poll`, `monitor_init` and `event_send` call when set (variant `fuzz/fp/src_hooks`).
   - A commands-and-CAN build saves 516 B flash and 144 B RAM at M0+ -Os (996 B at -O2). A plotting build costs 156 B and 20 B.
   - Byte-identical output (323/323 tests, a fuzz TX dump `cmp` equal).
2. **Overflow half only** (`src_ovfhook`): 312 B and 16 B saved on core, 80 B and 12 B added on plot builds.
3. **Leave as is.**

Recommendation: 1, if command-only boards are a real target (charger_control and relay_control look like ones). Otherwise 3.

### OP-5 (FUZZ-5): what happens to received bytes above 0x7F?

1. **Keep ASCII replacement, count it, state it.**
   - A per-port `rx_replaced` counter on `/status` and `mcu ports`, plus a sys row latched per episode, as for oversized lines.
   - SPEC 2.2 says debug text is stored as ASCII with U+FFFD for each byte above 0x7F.
2. **Store faithfully** (`backslashreplace`, as `\xb0`).
   - Byte values survive for diagnosing a baud mismatch. A `\` in the stored text becomes ambiguous, and every regex and export sees escapes.
3. **Decode UTF-8 with replacement.**
   - `25.3°C` survives. C1 and bidi controls can then arrive from a board, so `visible()`, the web UI and exports all need the bidi `<U+XXXX>` treatment the session names have.

Recommendation: 1. It keeps the one property that makes device text safe on a terminal, and satisfies "counted or announced" at the cost of a counter.

### OP-6 (AGENTUX-1): where does `mcu daemon start` take its bind address from?

1. **Config for the bind, URL for the port.**
   - `start` passes `--port` from the URL, since the CLI must find the daemon there, and refuses with both values named when the config's `server.port` differs.
   - It passes no `--host`, so the config's host stands (a `0.0.0.0` bind still answers the URL's loopback host). It refuses when the config's host is a specific address that differs from the URL's.
2. **The CLI's default URL comes from the config.**
   - With no `--url` or `MCUSCOPE_URL`, every `mcu` command reads `[server]` from the daemon config, so start and status agree. This couples every command to the config file.
3. **Document only:** "start binds the URL's address; the config's `[server]` applies to `mcuscoped` run directly."

Recommendation: 1. It fixes the README "LAN access" recipe and the restart-keeps-old-port case, and never overrides silently.

### OP-7 (AGENTUX-2): should a `-p` read see the daemon's own rows (port `''`)?

1. **Yes.**
   - `-p X` matches `port IN (X, '')`, so markers made without `-p`, session boundaries and daemon start/stop appear in every board's view.
   - SPEC 3.5 and 4 and the guide say so. Counts and exports under `-p` grow by those rows.
2. **No, but `mark` takes the addressed port.** `mark` with no `-p` stamps the only attached port; with several it stays `''`.
3. **Guide only:** PITFALLS says `mark` takes `-p` and that `-p` hides daemon-level rows.

Recommendation: 1. A boundary marker is context for every board, and the guide's own agent pattern then works unchanged.

### OP-8 (AGENTUX-3): when does finished `lines`/`tail -n` output carry `[port]`?

1. **The stream rule:** attached ports plus ports with stored rows. The output shape is then stable across runs. SPEC 4:1166 changes.
2. **Per result, as now.** The guide is corrected and tells agents to pass `-p` or `--json` across boards.

Recommendation: 1. An agent reading an untagged hit across two boards acts on the wrong board.

### OP-9 (AGENTUX-5): what is a verdict whose window had shed rows (`dropped > 0`, `checked > 0`)?

1. **A distinct status `incomplete`, exit 1, like `empty`.** `--allow-dropped` opts back in. SPEC 3.4 already tells the caller to retry rather than trust such a window.
2. **Keep `pass`, document it:** the guide says to check `dropped == 0`.

Recommendation: 1. It is the class 85 shape: a forbid-only pass over holes is the dangerous direction, and exit codes are what the guide tells agents to key on.

### OP-10 (AGENTUX-6): should CLI JSON errors carry a machine-readable cause?

1. **Add `kind`** from a fixed vocabulary at the `die()` sites:
   - `ambiguous_port`, `no_such_port`, `port_disconnected`, `no_such_session`, `bad_regex`, `usage`, `unreachable`, `daemon_error`.
   - Documented in the guide and SPEC 4.
2. **Text only.** Strip the `error: ` prefix and map pydantic paths to flag names (both fix-now under either option).

Recommendation: 1. The agent is the primary consumer, and several causes share exit 1 (class 70).

### OP-11 (AGENTUX-9): which JSON shape changes? (multi-select)

1. **`plot export --json` without `-o`:** `rows` becomes a list of objects; the CSV string goes. Recommended.
2. **Exact-decimal scaled values.**
   - At decode, a value whose `*scale` has an integer reciprocal k is `raw / k`, not `raw * scale`. That is the nearest double to the decimal, so `8.7` prints as `8.7`.
   - Both engines change (`protocol.py:911`, `plots.js:250`) with a shared fixture (class 19). Recommended.
3. **JSONL streams end with `{"truncated": true}`** when cut. This breaks "every line is a row"; not recommended. Instead, the guide documents the stderr `note:`, which is fix-now.
4. **Parsed fields for `can stat`, `adc read` and `cmd info`.** The firmware strings are SPEC's contract, and parsing them is a new feature; not recommended.

## Fix now

| Finding | Fix | Class | Batch |
|---|---|---|---|
| FUZZ-1 | Fold the line (`fold_breaks`) in `_submit_rx_line` before `classify`, so the daemon decodes the stored text. Add `\r\r\n` and mid-line CR cases to `plot_grammar_cases.json` and a CAN case, driven through ingest. | 19, 60 | link |
| FUZZ-4 | Restate SPEC 5.1 and INTEGRATION.md footprint and RAM from `fuzz/fp/matrix.sh` after OP-4 lands. | doc | firmware |
| SEC-3 | `_csv_cell`: also prefix `'` to a formula character following `;` or TAB inside a guarded cell. Update the SPEC 3.4 sentence (`SPEC.md:806`). `can.js` `csvField` sees only `ALIAS_RE` names and hex, so it stays. | new N10 | server |
| SEC-4 | ASGI middleware: 413 above 64 KB of `Content-Length` and of streamed chunked bytes. Cut `got` in `_validation_error` to 80 characters. | new N5 | server |
| SEC-5 | `visible()`: when an SGR was kept, append `\x1b[0m` before each LF and at the end. This stays within ruling API-7. | new N6 | cli |
| SEC-6 | POSIX only, at creation, never chmod an existing file. Data dir and `db_path` parent 0700 (`store.py:747`, `pidfile.py:64`, `_stdio.py:372`, `lockfile.py:122`). Create the DB 0600 before `connect`, so SQLite copies the mode to `-wal`/`-shm`. Lock file 0600. | new N7 | store, daemon |
| SEC-7 | Key `_fails` by the /64 for IPv6, the address for IPv4. | new N8 | server |
| RES-1 | Writer `busy_timeout` of a few ms. On `SQLITE_BUSY`, keep the batch and back off with a yield; never fall back row by row. Store `db_locked_since`, a sys row when the episode clears, and log once. | 1, 12, new N13 | store (+ server `/status`) |
| RES-2 | Record `(st_dev, st_ino)` of `db_path` at start and compare on each sweep tick, off the loop. On mismatch or ENOENT, stop writing, close the writer and the read connections, log an error, set `capture_error`, and make `writer_alive` false. | 12, new N1 | store (+ server `/status`) |
| RES-3 | `_unstorable` keeps count and first/last ts. When the episode clears, write "port X: N rx lines could not be stored between A and B: reason". Log the first and the closing count only. | new N2 | link |
| RES-4 | A now-anchored `last_ms` window also takes `ts <= now + skew`. Fix `now` once per request (class 44). Reword the late-rows sys row (`store.py:1187`) for both directions. Update the SPEC 3.4 window paragraph. | 77 (host side) | store |
| RES-5 | Stream into a sibling temp file and `os.replace` it onto the target on completion only. Covers `_OutFile`, `Client.download` and `plot export`. | 49 (sweep gains signals) | cli |
| RES-6 | Add a `lines_expired` counter, and write one sys row per sweep that deleted rows ("storage: expired N lines older than T"). | new N3 | store (+ server `/status`, cli `status`) |
| RES-7 | At start, when the previous run left an open session or its newest sys row is not `daemon stop`, write the sys row "previous run ended without a clean stop after line N (time); lines in flight were lost". | new N3 | store |
| RES-8 | Catch `sqlite3.DatabaseError` in `Store.start` and fail with "capture PATH is unreadable (msg): move it aside or set storage.db_path", exit 3, no traceback. | 9 | store |
| RES-9 | On the minute tick run `PRAGMA wal_checkpoint(PASSIVE)`. When it repeatedly cannot reach the end of the log and the WAL is above `journal_size_limit`, announce once: sys row and log. | new N13 | store |
| RES-10 + MODULES-1 | `CaptureLock` locks `realpath(db_path) + ".lock"`. After `flock`, compare `fstat(fd)` with `stat(path)` and retry on a mismatch. Add `verify()`, called from the store's sweep tick through `Store.add_tick_check(fn)`. The docstring says hard links are uncovered. | new N1 | daemon (+ store hook) |
| MODULES-2 | Skip `CaptureLock` when `db_path` is `:memory:` (or `""`). | 52 | daemon |
| MODULES-3 | Add a window `resize` listener in `app.js` that calls `applySideWidth()`; the expanded share follows from it. Pin `sideWidthFor` in node, and the DOM half with a Playwright check. | 76 | webui |
| MODULES-4 | Use one drag-end handler on `pointerup`, `pointercancel` and `lostpointercapture` for both dividers, and set `touch-action: none` on them. | new N12 | webui |
| AGENTUX-4 | Daemon: `/wait` ends the window when its cmd-mode send is answered `err` or `timeout`, with `status: "send_failed"`, as ruling CLI-2 already does for `/assert`. CLI: exit 1, and the text does not print the matched line as success. Update SPEC 3.4. | 85 family | server, cli |
| AGENTUX-6 (part) | Strip a leading `error: ` from the JSON `error` field in `die()`. Map pydantic `loc` names to CLI flags where the CLI posts them. Put the 16-pattern cap in the guide. | 70 | cli |
| AGENTUX-7 | Add one clause per message naming the next step, at these sites: `cmd` timeout (port, command, `--timeout`/`mcu status`), `wait` timeout on a disconnected port (append its state from `/ports`), `can dump -f` (say "daemon unreachable" on the first failed poll), and the server's `timeout_ms` wording (also "(--timeout on the CLI)"). | none | cli, server |
| AGENTUX-8 | Correct each guide and snippet line: `--session` with `--timeout`, `--last-ms` on an ended session, the `mark`/`wait` example (`--chan marker`), `ping` output, "one session per run", non-unique names and ids, purge's non-tty refusal. Every command validates an explicit `-p` against `/ports` (ruling CLI-3). | 31, new N9 | cli |
| AGENTUX-9 (part) | `purge -y --json` with nothing left reports `dry_run: false` and `deleted: 0` (class 17). `send --json` returns the tx echo's `line_id` (route in server; the link returns the echo row). The guide documents the JSONL truncation `note:`. | 17 | cli, server, link |
| AGENTUX-10 | `session list --limit 0` says "--limit 0 lists no sessions", not "none recorded". `session delete` says "N lines deleted". The per-command `--limit 0` meaning goes in the notes and guide. A daemon-level row renders `[-]`. Warn on a `--names` entry no row carries. EMPTY goes to stdout with PASS and FAIL. | none | cli |

## Fix batches (partitioned by file)

| Batch | Tier | Files | Findings | Waits on |
|---|---|---|---|---|
| store | opus-medium | `store.py` | RES-1, 2, 4, 6, 7, 8, 9; SEC-6 (DB and parent); `add_tick_check` hook; OP-7 port filter; OP-2 summary (option 2) | OP-2, OP-7 for those parts |
| server | opus-medium | `server.py` | SEC-3, 4, 7; AGENTUX-4 (daemon), 7 (wording), 9 (`/send` line_id route); `/status` fields `lines_expired`, `db_locked_since`, `capture_error`; OP-1 (SPEC 3.1), OP-2 `/plot/channels`, OP-9 verdict | OP-1, OP-2, OP-9 for those parts |
| link | sonnet-medium | `serial_link.py`, `link.py`, `protocol.py` | FUZZ-1, RES-3, `send` echo row for AGENTUX-9; OP-5 counter, OP-2 decoder cap, OP-11.2 (Python half) | OP-2, OP-5, OP-11 |
| daemon | sonnet-medium | `daemon.py`, `lockfile.py`, `pidfile.py`, `_stdio.py`, `config.py` | RES-10 + MODULES-1, MODULES-2, SEC-6 (lock and dirs) | store's `add_tick_check` name (fixed here) |
| cli | opus-medium | `cli.py`, `cli_output.py`, `cli_client.py`, `cli_daemonctl.py`, `render.py`, `docs/CLAUDE_SNIPPET.md`, `host/README.md`, `README.md` | SEC-5, RES-5, AGENTUX-4 (client), 6, 7, 8, 9, 10, `status` shows the new fields; OP-1 (README), OP-3, 6, 8, 9, 10, 11.1 | OP-6, 8, 9, 10, 11 |
| webui | sonnet-medium | `app.js`, `style.css`, `plots.js` | MODULES-3, 4; OP-11.2 (JS half) | OP-11 for `plots.js` |
| firmware | sonnet-medium | `firmware/monitor/*`, `firmware/tests/*` | OP-3 (FUZZ-2), OP-4 (FUZZ-3), then FUZZ-4 restated | OP-3, OP-4 |

- Interface names fixed here so batches can run in parallel:
  - `Store.lines_expired`, `Store.db_locked_since` (float or None), `Store.capture_error` (str or None), `Store.add_tick_check(fn)`.
  - `CaptureLock.verify()` raises `lockfile.LockLost` (with its message) on failure; a tick check reports failure by raising, and the store catches it, sets `capture_error` to the message and stops writing.
- Toolchain for the firmware batch:
  - Reuse `~/tt-data/mcuscope-2026-10-05/fuzz/fp/` (`build.sh`, `matrix.sh`, `src_hooks`).
  - arm-none-eabi-gcc 13.3 lives at `/opt/st/stm32cubeide/plugins/com.st.stm32cube.ide.mcu.externaltools.gnu-tools-for-stm32.13.3.rel1.linux64_1.0.100.202509120712/tools/bin`.
- Shared documents go by section, re-read and retried on a failed anchor:
  - SPEC: link 2.1/2.2/2.5; firmware 2.5 CAN and 5.1; server 3.1 and the 3.4 routes; store 3.2/3.4 windows and storage; cli 4; webui 9.
  - INTEGRATION.md goes to firmware.
  - The guide (`AI_GUIDE` in `cli.py`) belongs to cli; server and store hand it their wording in their reports.
- Existing tests that pin changed behaviour are listed in each batch's "not done", per REVIEW.md "Fix batches". Expected: `test_can_id_mask` (OP-3), the `/wait` JSON shape, `_csv_cell` cases, `visible()` cases and the `_port_column` tests.

## New classes proposed

| Id | Name | Findings | Sweep |
|---|---|---|---|
| N1 | A guard keyed on a path spelling rather than the file's identity | MODULES-1, RES-2, RES-10 | `grep -n "db_path" host/mcuscope/*.py`: every key, compare or lock built from the string, and every file held by handle while others re-resolve the path |
| N2 | An episode notice written through the path whose failure it reports | RES-3 | every `_EpisodeNotice` user and every sys row written on a store-failure path |
| N3 | Captured data deleted or altered with no counter or notice | RES-6, RES-7, FUZZ-2 (mask), FUZZ-5 | every `DELETE` in `store.py`, and every lossy transform on ingest and in the monitor (`&`, clamps; the dlc clamp at `monitor.c:1046` is one) |
| N5 | An untrusted request body buffered or echoed whole | SEC-4 | every route's body model and every error that echoes input |
| N6 | A kept escape sequence whose state outlives its line | SEC-5 | every `visible()` caller and any other pass-through of control sequences |
| N7 | A file or directory created with the default mode | SEC-6 | every `open`/`os.open` with `O_CREAT`, `makedirs`, `mkstemp` and `sqlite3.connect` creating a file |
| N8 | A throttle keyed finer than the attacker's allocation unit | SEC-7 | every per-client table (`_fails`, the subscriber cap) |
| N9 | A guide behaviour claim nothing executes (widens 58 from names to behaviour) | AGENTUX-3, AGENTUX-8 | run every `ai-guide`, README and snippet example against the sim, and assert its exit code and the claimed output |
| N10 | An injection guard judged at one delimiter when readers split on several | SEC-3 | `_csv_cell`, `csvField` |
| N12 | A pointer gesture ended only by `pointerup` | MODULES-4 | every `pointerdown`/`setPointerCapture` in `webui/*.js` |
| N13 | Another process on the capture, unannounced | RES-1, RES-9 | every SQLite wait or growth an external connection can cause: busy timeouts, WAL growth, the lock |
| N4 | Daemon state keyed by wire-chosen names with no cardinality cap | SEC-2 (once OP-2 is ruled) | daemon dicts and sets keyed by device data (the security leg's grep found only `_plot_summary`) |

Widened, not new: 49 (the sweep counts signal termination as non-completion), 76 (a container resize is an input), 77 (the host-side `last_ms` window after a step back).

## The two questions

1. Least confident:
   - RES-2's fix. The resilience leg did not establish which connection leaves the `-wal` paired with the restored file: the writer by handle, or read connections reopened by path. The store batch must drive both replace orders before and after the fix, not reason about it.
   - The cross-batch names above: one rename in the store batch breaks server and daemon.
2. Not yet checked:
   - The dlc clamp beside FUZZ-2's mask (`monitor.c:1046`) is the same silent-alteration shape; OP-3's answer should be applied to it deliberately.
   - The plot value fix (OP-11.2) must not touch `_fmt_num`, which also prints `ts`: `.15g` there would cut 17-digit timestamps.
   - Nothing pins `-p` on commands that ignore it; the cli batch should sweep the click tree for every command that accepts the global `-p` and reads nothing from it.

## Owner rulings, 2026-10-05

- OP-1: 1, single user, stated in SPEC 3.1 and both READMEs; SEC-6 modes ship.
- OP-2: 1, cap at ingest per port, N = 256. Distinct names count per port within the daemon run; the summary rebuild keeps the 256 most recent names per port, so a long-lived capture with renamed channels does not lock out new ones.
- OP-3: 1, emit the id unmasked per SPEC 2.5; the dlc clamp follows the same rule.
- OP-4: 1, hooks for the plot registry and the overflow notice.
  - The existing `MON_NO_<FAMILY>` switches stay; INTEGRATION.md gains their measured savings (M0+ one bus core, flash at -Os / -O2): CAN 1140 / 1760 B (12 B RAM), I2C 480 / 536, GPIO 152 / 140, ADC 148 / 176, SPI 108 / 120, all five 2326 / 3178. Re-measure after the batch lands.
- OP-5: 1, keep the replacement, add `rx_replaced` and a sys row per episode, SPEC 2.2 states it.
- OP-6: one precedence for every `mcu` command, `daemon start` included: `--url`, then `MCUSCOPE_URL`, then the config's `[server]` host and port, then `127.0.0.1:8558`. A `0.0.0.0` bind connects via `127.0.0.1`.
- OP-7: 1, `-p X` matches `port IN (X, '')`. Verdicts (`/wait`, `/assert`) keep excluding the host's own rows (class 84).
- OP-8: 1, the stream rule for `[port]` tags.
- OP-9: 1, status `incomplete`, exit 1, `--allow-dropped` opts in.
- OP-10: 1, `kind` on JSON errors.
- OP-11: 1 and 2 (plot export rows as objects; exact-decimal scaling in both engines). Not 3 or 4.
- SOAK-1 (soak.md, MEDIUM): the `min_sessions` floor protects only ended sessions; lines of the running session older than `retention_days` expire, announced by the RES-6 sys row. SPEC and the `config.py` comment state it. Store batch.
- OP-9 on `wait` (asked 2026-10-05 after the cli batch): assert only. A wait timeout stays exit 2; the guide tells agents to retry a timeout whose `dropped` is non-zero.
- FD-CLI-2 (fixdiff-cli.md): `purge` refuses `-p` (exit 2, "purge removes every port's rows; -p does not scope it").
- FD-CLI-3/4: exports keep temp file plus rename; when the temp file cannot be created (read-only dir, name too long) the target is written directly with a stderr warning that an interruption leaves it partial. A failed rename names the target and removes the temp file. Hard links and ACLs: documented limit.
- FD-CLI-2 exit code: the refusal exits 1 with `kind: usage` (SPEC 4: refusals exit 1, 2 is timeouts); the "exit 2" in the option text above was the orchestrator's wording.
- FD-SERVER-7 (2026-10-06): a window with shed rows is decided when no forbid is given and every expect matched (`pass`); with a forbid it stays `incomplete`.
- FD-EDGE-2 (2026-10-06): a `!p`/`!ps` line refused past the plot name cap is stored on chan `debug`, so the web UI skips it; `--chan event` filters skip it too.
- V94-4 (2026-10-06): the data-frame DLC 9-15 clamp to 8 bytes stays; stated in SPEC 2.4 and INTEGRATION.md.
