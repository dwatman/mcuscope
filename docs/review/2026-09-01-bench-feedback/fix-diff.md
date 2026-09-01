# Fix-diff review: 9dd2290 (bench feedback 2026-09-01)

HEAD is 9dd2290, but the working tree is **not clean**: uncommitted edits to `docs/SPEC.md`, `host/mcuscope/{cli,serial_link,server,store}.py`, `host/tests/{test_port_health,test_sessions}.py` already fix six of the findings below. Each finding is marked OPEN or FIXED-IN-WORKTREE so a fix round does not redo work. Line numbers are against the committed 9dd2290 blobs.

## Findings

### F1. `--decode` primes the definition cache at the window's END, so a mid-window `!pd` mis-decodes everything before it - HIGH - OPEN
`host/mcuscope/cli.py:478` (`_decode_rows`): `newest = rows[-1]["id"]`, passed to `_make_decoder(..., id_to=newest)`, which primes newest-first from `^!pd ` rows at or before the window's **last** row.

Scenario: a run redefines sid 6 mid-window with the same total wire width, e.g. `!pd 6 a:u2` early, then `!pd 6 b:u2:mV`, then more samples. `mcu lines --last-ms 600000 --decode` covering both halves primes with the *later* def, then feeds rows oldest-first. Every sample from before the redefinition renders as `s6 b=100mV` instead of `s6 a=100`: wrong channel name, wrong unit, silently. When the widths differ the sample falls back to raw (visible), which is why the shipped test misses it: `test_timeline.py:177` only covers a redefinition *after* the window (u1 -> u4, width mismatch).

Fix: prime with `rows[0]["id"]` (the window's **oldest** row), which is the def in force at window start; the "redefinition after the window" case the docstring argues for is satisfied equally, because the oldest row is also before the reflash.

Registry: closest to class 26 (a view re-derived from state that has since rotated past it); the observable is class 12 shaped - the decode reads confident and is wrong.

### F2. `--last-ms` is re-evaluated per page, so a paged query silently drops rows at the old edge and still reports complete - MEDIUM - OPEN
`host/mcuscope/cli.py:412-431` (`_fetch_lines`) reuses the same `params` for every page, including `last_ms`. The daemon computes `now - last_ms` per request, so page 2's lower time bound is newer than page 1's by however long page 1 took.

Scenario: `mcu log export --last-ms 3600000` over a 500k-row hour. Pages 1..500 each take ~100 ms, so by the last page the window has slid ~50 s forward; rows in that 50 s at the far edge are never returned, the loop ends with `truncated` False, and the export claims to be complete. Same shape for `mcu lines --last-ms N --limit 5000`.

Fix: convert `last_ms` to an absolute `since_ts` once, before the first page (the endpoint already takes `since_ts`).

Registry: class 17 (the reported value is the request, not the result) - `truncated: false` reports the last page's answer, not the query's.

### F3. `--limit 0` no longer reports `truncated` - MEDIUM - OPEN
`host/mcuscope/cli.py:418` (`while len(rows) < limit`) never executes for `limit == 0`, so `_fetch_lines` returns `{"lines": [], "truncated": False}` without calling the endpoint.

SPEC 3.4 `/lines` states explicitly: "`truncated` still reports whether rows exist beyond those returned, so it is true for a non-empty window at `limit=0`." `mcu lines --limit 0 --json` on a busy capture now prints `truncated: false`, and `note_truncated` stays silent - the "no backfill, stream from here" probe lost its only signal that a backlog exists.

Registry: class 31 (a field accepted on every path and read on only some) inverted - the flag the endpoint exists to produce is discarded by the caller.

### F4. `parse_clock` uses `fromisoformat` as a stand-in for the documented `HH:MM[:SS[.mmm]]` grammar, and that grammar differs on the 3.10 floor - MEDIUM - OPEN
`host/mcuscope/cli_output.py:197`. Verified on 3.13:
- `--from 20260901` parses as the **time** 20:26:09.010, not as a date. A user who typed a compact date silently queries "today at 20:26".
- `--from 19` parses as 19:00:00 (bare hour), outside the documented grammar.
- `--from 19:53:35+09:00` yields a tz-aware `time`; `datetime.combine` carries the offset, so the meaning changes without warning.

Floor divergence (SPEC declares 3.10): `time.fromisoformat` before 3.11 accepts only what `isoformat()` emits, i.e. exactly 3 or 6 fractional digits and no `Z`. So `--from 19:53:35.25` and `--from 2026-09-01T12:00:00Z` work on 3.11+ and are a `BadParameter` usage error on 3.10 - the same command, two answers, and no test can see it on a 3.13 venv. `test_timeline.py:115` only exercises forms both versions accept.

Registry: class 22 (a stdlib predicate standing in for a wire grammar), with class 42's floor-divergence face. Fix: match the documented grammar with an explicit regex, then build the datetime.

### F5. An open **named** session pins the retention floor forever - MEDIUM - OPEN
New behaviour: `host/mcuscope/server.py:378-389` resumes a named session across daemon runs and never auto-closes it, and no automatic session is opened while one is open.

`store.retention_floor_id()` (store.py:1971) returns the `start_id` of the Nth newest session. With `min_sessions >= 1` and one named session left open on the bench, that session is permanently the newest, so `floor_id` sticks at its `start_id` and **age expiry can never delete anything from that point on**, for as long as it stays open - which is now indefinitely, since shutdown no longer closes it. The size cap still trims (via the forced path with its warning), so the symptom is "retention_days stopped working" plus a log line about protected sessions exceeding the cap.

Secondly, `min_sessions`' documented meaning ("the newest N sessions == the newest N daemon runs", SPEC 3.3.1) stops holding: subsequent daemon runs create no session row at all while a named one is open.

### F6. `mcu log export`'s new default is unbounded, buffered whole, and unpaced - MEDIUM - OPEN
`host/mcuscope/cli.py:1162`: `limit = limit or sys.maxsize`. On a 1M-row capture the default `mcu log export` issues 1000 sequential HTTP round trips, accumulates every row in `rows`, then builds one `"\n".join(...)` string - roughly two full copies of the capture in the CLI's RSS - with no progress, no cap and no way to interrupt cleanly other than Ctrl-C. SPEC 4 now blesses it ("every row by default"), so this is a design finding rather than a spec breach, but the old default was 1000 rows and nothing warns about the change. Note the same call path is what an AI agent reaches for first.

### F7. `--match` / `--chan` starve the live decoder of its definitions - LOW - OPEN
`host/mcuscope/cli.py:757-760`: in `_follow_ws` the `chan` and `pat` filters run **before** `_decoded_row`, and in `mcu lines` the filter is applied server-side. `mcu tail -f --decode --match vbat` therefore never delivers a `!pd` row to the decoder, so a firmware redefinition arriving during the follow is never learned and every sample after it decodes against the stale primed def (or, on a width change, drops to raw). Also: `--match` matches the **raw** text, never the decoded rendering, which is not stated anywhere.

### F8. `--from` after `--to`, and an overnight window, are silent empty results - LOW - OPEN
`_clock_bounds` (cli.py:437) applies `since_ts` and `id_to` independently and never compares them. `mcu lines --from 19:00 --to 18:00` and the overnight `--from 23:50 --to 00:10` (both resolve against `date.today()`) return zero rows with exit 0, indistinguishable from a genuinely empty window.

### F9. `_clock_bounds` reads `first[0]["id"]` unguarded - LOW - OPEN
`host/mcuscope/cli.py:449`. Every other new row access in this commit is shape-guarded (`page[-1].get("id") if isinstance(page[-1], dict)` at cli.py:428). A daemon answering `{"lines": [{}]}` is a `KeyError` traceback out of `mcu lines --to ...`. Registry: class 9 (CLI exit-code contract), the `_list_field` face noted there - the container is vouched for, the contents are not.

### F10. `--retry-ms` bounds the gap between attempts, not the wait - LOW - OPEN
`host/mcuscope/cli.py:1193-1206`: the deadline is only tested after a response has come back, so `mcu cmd 'x' --timeout 30000 --retry-ms 500` can block for 30.5 s. The help text says "Retry an `ERR 6 busy` answer for up to this long."

### F11. `_run_cmd`'s loop terminates only because `emit_cmd_result` raises - LOW (latent) - OPEN
Same site: there is no `return`/`break` after `emit_cmd_result(s, res)`, and no comment saying the call always raises `typer.Exit`. Any future path through `emit_cmd_result` that returns normally turns this into a 50 Hz spin against `/cmd`. One-line fix: `return` after the call.

### F12. No opt-out for the connect-time `ping`, and it perturbs write health on a flapping link - LOW - OPEN
`host/mcuscope/serial_link.py:644` (`_identify`). Every connect writes a line to the target - there is no config flag to suppress it for firmware that is not a monitor (SPEC 3.4's raw escape hatch exists precisely for such targets). On a replug loop each reconnect also consumes a seq, files a `cmd` row, and - on a port whose TX is dead - sets `write_failures = 1`, so `mcu status` flips to `DEGRADED` from the daemon's own probe rather than from anything the user did. It holds `_cmd_lock` for up to `IDENTIFY_TIMEOUT_MS` (1 s), delaying a user command issued immediately after a reconnect.

### F13. `plot channels --active 0` can never match - LOW - OPEN
`host/mcuscope/cli.py:1506`: `now - last_ts <= active` with `min=0` on the option, so `--active 0` selects only channels timestamped in the future and prints "no active plot channels". A bound that is accepted and cannot be satisfied.

### F14. Firmware: the rejection latch reports the first reason only - LOW - OPEN
`firmware/monitor/monitor.c:673-683`. `g_plot_rejected` clears only in `monitor_init`, so a sid rejected once for `len`, then registered successfully, then rejected for `body` is silent. This matches SPEC 2.5's "once per sid", so it is a documented limitation rather than a breach - worth one line in `monitor.h` since the whole point of the notice is that a rejection is otherwise invisible.

### F15. SPEC does not describe the ad-hoc `!p` rendering - LOW - OPEN
SPEC 4 says `--decode` renders `!ps`/`!p` rows as `s<sid> name=value ...`. For an ad-hoc `!p` line `cli_output.py:258` emits a `p:<name>,<name> ...` prefix instead. Also undocumented: `--names` or `--changes` alone silently enables decoding (`_make_decoder` at cli.py:452 keys on `decode or changes or names`), where SPEC describes both as "with `--decode`".

### F16. ai-guide gaps - LOW - OPEN
The guide gained `--from/--to` and `--decode` examples but never says the clock forms are **today, local**, does not mention `log export`'s change from 1000 rows to every row, `plot channels --active`, or the new `!e` line (`mcu lines --match "^!e"` is the SPEC 2.5 remedy and the guide is where an agent would look for it).

## Already fixed in the uncommitted working tree

- **F17 - `_captured_traffic` counts the connect-time ping - MEDIUM.** `store.py:1139` at 9dd2290 has `chan IN ('debug','cmd','resp','event')`. The new auto-ping files a `cmd` row on every connect, so from this commit an automatic session with a board attached is *never* dropped, breaking SPEC 3.3.1's "an automatic session that recorded no device traffic is dropped". Worktree removes `'cmd'` and amends SPEC; `test_sessions.py:815` covers it.
- **F18 - class 40 torn write-health read - MEDIUM.** `serial_link.py:999-1010` wrote `write_failures`, `last_write_error`, `last_write_error_ts`, `_write_fail_since` as four separate stores from the `asyncio.to_thread` worker while `status()` read them on the loop; a `/status` landing mid-update pairs one failure's count with another's message, and the success path reset `write_failures` while leaving `last_write_error` non-null (contradicting SPEC's "the next write that lands resets it"). Worktree replaces them with a frozen `_WriteHealth` swapped in one store. This is a direct repeat of registry class 40 (pjstream, 2026-08-28) in code written after the class was filed.
- **F19 - `mcu status` mislabels the streak start - MEDIUM.** `cli.py:192` printed `write failures since <last_write_error_ts>` - the **most recent** failure - while the daemon's own `/cmd` message uses `_write_fail_since`, the **first** of the streak. Same word, two times, and `_write_fail_since` was not exposed on `/status` at all. Worktree adds `write_failing_since` to `status()` and SPEC and reads it.
- **F20 - a stale automatic session is never closed with `auto_session = false` - MEDIUM.** `server.py:389`: the `elif config.storage.auto_session` chain leaves a crashed run's open auto session open, and the comment ("start_session below closes a stale one") describes a branch that does not run. Worktree adds the `elif open_session is not None: await store.stop_session()` arm; `test_sessions.py:844` covers it.
- **F21 - unbounded `^!pd ` regex scan for decoder priming - MEDIUM.** `cli.py:452-470` queried `/lines?match=^!pd ` with no lower bound and no `chan`, so on a 1M-row capture with no plot streams every `--decode` invocation walked the whole table against the store's regex budget - the exact reason `serial_link.PLOT_DEF_LOOKBACK` exists (class 1/20). Worktree adds `DEF_LOOKBACK = 20000`, `chan="event"`, and resolves the newest id for the live path.
- **F22 - `--to` before every captured row still returns row 1 - MEDIUM.** `cli.py:449`: `max(first[0]["id"] - 1, 1)` clamps to 1, which *includes* id 1, so `--to 09:00` against a capture starting at 10:00 printed the first row. Worktree adds the explicit "nothing precedes --to" flag; `test_port_health.py:200` covers it.

## Checked and ruled correct

- `firmware/monitor/monitor.c` `plot_reject`: `1u << (sid - '0')` cannot shift by a negative or out-of-range amount - `monitor_plot` gates `def->sid` to `'0'..'9'` at line 690, before any call. Max shift 9, fits `uint16_t`.
- `monitor_eventf` uses `vsnprintf`, so `%c` is real; `write_line` sanitises non-printables and appends the single LF, and `"!e plot X badarg body"` is far inside `MONITOR_LINE_MAX`. No `g_out` reentrancy: every `plot_reject` path returns before `monitor_plot` touches `g_out`, and `emit_pd` runs only after.
- `monitor_init` clears `g_plot_rejected`; the latch is bounded to 10 sids, so a full stream table cannot spam.
- `LineDecoder._fields` cannot exhaust `points`: `decode_plot_sample` emits exactly one point per non-`None` lane, and `_parse_bit_lanes` rejects an all-`None` lane list and an empty channel list, so a "bits channel with every lane None" or a "def with zero channels" cannot reach the decoder.
- `_fmt_value` on nan/inf: `is_integer()` is False, `%.6g` gives `nan`/`inf`; `-0.0` gives `0`; the `abs(v) < 1e15` guard keeps large floats out of `int()`. `parse_plot_value` already refuses non-finite anyway.
- `LineDecoder.decode` on a sample ahead of its def, a non-plot line and a CAN event: all pass through raw (`test_timeline.py:191`).
- `clock_option` running twice (callback plus `_clock_bounds`) is harmless - `parse_clock` is pure apart from `date.today()`, and a midnight straddle would need a sub-second race.
- `_fetch_lines` page arithmetic: exact-multiple, over-supply and under-supply cases all terminate with the correct `truncated`; `id_to` strictly decreases so no row is returned twice; a page whose rows carry no ids breaks out safely.
- `since_id` + paging: `query_lines` with `order=desc` returns the newest N above `since_id`, so walking `id_to` downwards converges on `since_id` correctly.
- `mcu lines --json` order: `rows[::-1]` restores the API's newest-first order, and `/lines` returns no keys beyond `lines`/`truncated`, so nothing is lost by rebuilding the body.
- `_tail_snapshot` computes its dedupe watermark from the fetched rows *before* decoding, so a dropped `!pd` or an unchanged sample cannot lower it.
- `_follow_ws` staged-backfill replay goes through the same `handle()` as live frames, so staged rows are decoded and `--changes` state is continuous across the snapshot/follow seam (the class 16 "staged twin" trap is not present).
- `_run_cmd` with `--json` prints exactly one object: `out_json` runs only inside `emit_cmd_result`, which is reached once (class 10 holds).
- `_run_cmd` retrying a side effect is safe: `ERR 6 busy` means the command was not executed.
- `_identify` error handling: a timeout comes back as `{"status": "timeout"}` from `send_command` rather than raising, so `target` stays `None`; `PortError`/`StoreError` are caught; `CancelledError` correctly propagates so `stop()`'s `_bg_tasks` barrier can cancel it. `self._loop` is never `None` on this path - `_on_connect` already calls `_spawn_sys`, which needs the loop, two lines earlier.
- `_identify` cannot run on a held port: `_on_connect` fires only on a real connect, and `self.target` is cleared on both connect and disconnect.
- `store.active_session()` synchronously on the loop is precedented - `_stop_session_locked` already calls it from awaited loop code, and it is a single indexed one-row read.
- `open_session["auto"]` truthiness works for both the `int` and `bool` shapes `_session_dict` can produce.
- `retention_floor_id` itself is unchanged and correct; the issue in F5 is the new lifetime of an open named session, not the query.
- `plot channels --active` correctly excludes a channel whose `last_ts` is null (`or 0` makes the age huge), and preserves the rest of the body under `--json`.
- `fmt_age` clamps a negative (clock skew) to `0s`.
- SPEC's paging sentence names exactly the three commands that page (`lines`, `tail`, `log export`); `mcu can dump` is on `/can/frames` and is still capped, which the SPEC sentence does not claim otherwise. `tail` correctly has no `--from/--to` in the table.
- SPEC's `/cmd` promise "names the streak from the second failure on" matches `if self.write_failures == 1 / else` in `_write_bytes`.
- `!e` is stored as a generic event by the existing unknown-`!` path; nothing in the host parses it, as SPEC 2.5 states.
