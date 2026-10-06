# Fix-diff review, third pass, area outer3 (2026-10-06)

Scope: fix3.md (cli_output reserved names, `mcu status` move-aside wording, guide lines), fix4-link.md, fix4-cli.md, and the orchestrator's settable clock in `test_serial_link_rx_ingest.py::test_an_unstorable_episode_names_its_first_and_last_drop`.
Scratch: `~/tt-data/mcuscope-2026-10-05/fixdiff-outer3/` (`serial_link.py.orig`, `mutA.txt`, `mutD.txt`, `probe1.txt`, `files.txt`).
Probes ran as a temporary `host/tests/test_zz_fixdiff3_probe.py`, since deleted; `git diff --stat` matches the starting tree (58 files).
No daemon started.

## Findings

### FD-OUTER3-1 LOW CONFIRMED: `--last-ms` with a `--from` past the new ceiling is refused with "until_ts is before since_ts"

- `host/mcuscope/cli.py:833-835` (`_absolute_window`).
- Failure: the new ceiling (`anchor + 10 s`) can be lower than a `--from` the user typed. The CLI then sends an inverted pair, and `_check_window` (`server.py:3745`) refuses it.
  - Before the fix this was an empty answer, exit 0.
  - Now it is exit 1 with `error: until_ts is before since_ts`, naming a bound the user never gave.
  - This happens on `lines`, `log export` and `can dump`.
  - The realistic form is `--session <ended> --last-ms N --from <after its end>`.
- Repro (probe, in-process app):
  - `mcu lines --last-ms 60000 --from <now+2 min>` gives rc 1 with that error.
  - `mcu lines --from <now+2 min>` gives rc 0 and empty output.
  - `--session old --last-ms 60000 --from <end+10 min>` gives rc 1 on all three commands.
- A `--to` earlier than the `--last-ms` floor was already refused the same way before this fix, so the same change covers it.
- Suggested fix: after both ends are computed, return `until_ts = max(until_ts, since_ts)` when both are set. The window is then empty, as the intersection is, and nothing is widened because `since_ts` is strict. Add a test with `--from` past the ceiling asserting rc 0 and no rows.
- Class 104 (the fix's own side effect).

### FD-OUTER3-2 LOW CONFIRMED: an unstorable episode closed at stop, with the store still failing, reaches only the log, and only as a count

- `host/mcuscope/serial_link.py:945-955` (`_close_unstorable`), `:471-472` (`stop()`), `:1084-1093` (`_store_sys`).
- Failure: the row goes through the store that is failing. `_store_sys` swallows the `StoreError` at DEBUG, so the row text, which holds the first and last times and the cause, is lost.
  - The surviving WARNING lines are the episode's opening one (with the cause) and `port board: 2 unstorable rx lines in the episode just ended`, which has no window.
  - SPEC 2.2 says "An episode still open at detach, reconnect or shutdown is recorded then, and logged", which overclaims for the case class 93 is about.
  - `test_an_open_unstorable_episode_is_recorded_at_stop` restores the store before `stop()`, so it never drives the failing-store path.
- Repro (probe): `submit_line_nowait` and `add_line` both raise `StoreError`, then `_ingest(b"a\nb\n")` and `port.stop("detach")`.
  - Result: no "could not be stored" sys row.
  - Log: `[WARNING dropping unstorable rx line: disk is full, WARNING 2 unstorable rx lines in the episode just ended, DEBUG sys row dropped: disk is full]`.
- Suggested fix:
  - When `stopping`, `log.warning` the full row text, as V93-2 does for stranded lines.
  - SPEC: "recorded if the store takes it, and logged with its window".
  - Add a test with the store still failing at stop that asserts the logged text carries the times.
- Class 93.

### FD-OUTER3-3 LOW CONFIRMED: the "announced once" eviction assertion does not catch a warning per eviction, and the warning points at a counter nothing exposes

- Test: `host/tests/test_reconnect.py:459-460`. Code: `host/mcuscope/serial_link.py:1541-1549`.
- Failure 1: the assertion filters on `"counters of 'a0' dropped"`, so the warnings for `a1..a19` never match it.
  - Mutation: `if self.carried_evicted == 1:` replaced with `if True:`.
  - Result: `test_carried_counters_are_bounded_and_evict_the_oldest` still passes (`mutA.txt`: 1 passed).
- Failure 2: the warning says "further evictions are counted in carried_evicted", but `carried_evicted` is on no `/status` field, no CLI output and not in SPEC. Only the test and the once-latch read it, so the pointer leads nowhere.
- Suggested fix:
  - Assert on every record containing `"detached aliases: counters of"` (exactly one).
  - Either put the count on `/status` (and SPEC 3.4), or reword the warning to "further evictions are not logged" and keep the attribute as the latch only.
- Class 95.

### FD-OUTER3-4 LOW SUSPECTED (Windows): the reserved-name branch overrides a successful stat and may write a real file in place

- `host/mcuscope/cli_output.py:223-228, 250-255`.
- Failure: `device = os.name == "nt" and is_reserved_name(real)` wins even when `os.stat` returned a regular file.
  - Windows 11 no longer treats some reserved names with an extension or a directory (`out\con.csv`) as devices. On such a system that export is a real file, written in place: a killed export leaves it partial, with no warning.
  - FD2-OUTER-1 depended on `os.stat("NUL")` raising, which was never confirmed. To my knowledge, CPython 3.8+ answers `S_IFCHR` for `NUL` (`\\.\NUL` after `realpath`), which the existing `not S_ISREG` branch already handled.
  - The set also omits `CONIN$`, `CONOUT$` and the superscript `COM¹²³`/`LPT¹²³` that `ntpath.isreserved` lists, and the test pins `COM0` as ordinary, which Microsoft's naming page lists as reserved.
- Repro: needs Windows; reasoned only.
- Suggested fix: first run `python -c "import os; print(os.stat('NUL'))"` and `mcu lines -o NUL` on the desktop. If stat succeeds, the name branch is redundant and can be deleted. Otherwise consult the name only when `st is None`.
- Class 92 (a guard keyed on a path's spelling rather than the file).

### FD-OUTER3-5 LOW CONFIRMED (guide): `plot_name_refused` still described as `!p` only

- `host/mcuscope/cli.py:3164-3167` (`AI_GUIDE`: "!p lines past the 256 ad-hoc names per port") and the comment at `cli.py:266`.
- Failure: since V95-1 the cap also refuses `!ps` samples (SPEC 2.5 and 3.4 now say so), but the guide an agent reads does not. fix4-link listed this under "Not done" and nobody picked it up.
- Fix: "plot lines (`!p`, `!ps`) past the 256 plot names per port".
- Class 100.

### FD-OUTER3-6 NIT: the guide says the cause "ends" with the move-aside phrase; it does not

- `host/mcuscope/cli.py:3157-3159` against `store.py:915-917`. The store's text continues ", or the restart replays it into the file now at that path", so "ends" should be "contains".
- The CLI keys on the substring `"aside before restarting"`, and two tests each hand-copy that literal (`test_cli_status_capture_health.py:72`, `test_store_capture_wal_aside.py:25`). If the store message is reworded, `mcu status` silently drops back to "restart the daemon", which replays the WAL into the replacement file. One test that feeds the store's real `capture_error` to `mcu status` would tie the two together.
- Classes 100 and 58.

## Checked and fine

- `_absolute_window` anchor: floor and ceiling now come from one value.
  - That value is `/status` `now`, or the ts of the newest row at or below an ended session's `end_id`, which is the end marker.
  - The daemon anchors the same way: `newest_ts_at_or_below(id_to)` with no port filter, and the CLI lookup sends no port either.
  - `/sessions?name=` resolves numeric ids as `session=` does, so a session given by id is not anchored at now.
- `--to` earlier than the ceiling: `min` keeps it (`test_an_earlier_to_still_bounds_a_last_ms_window`). A later `--to` does not lift the ceiling (`test_a_later_to_does_not_lift_the_last_ms_ceiling`).
- `can dump -f --last-ms`: `_dump_follow` builds its params from `_can_params` alone, with no `until_ts`, so live frames are not bounded. The backfill's ceiling drops only frames stamped ahead.
- `log export --limit` with `--last-ms` now pins `id_to` through `_pin_ceiling`, so rows that arrive during the export are left out. This is a consistent snapshot, and correct because the pinned id is the highest one with `ts <= until_ts`.
- `WINDOW_TS_SLACK_S`: `cli.py:839` equals `store.py:243`, pinned by `test_the_cli_slack_is_the_daemons`.
- Typed-name cap (V95-1):
  - One set, one counter and one latch for `!p` and `!ps`. A `!pd` is not counted itself.
  - `plot_name_refused` counts refused lines (samples), as SPEC 3.4 now says.
  - `test_typed_names_count_against_the_same_cap` checks the set size, the refusal count, the plotted names and the notice text.
- Orchestrator's settable clock:
  - Every wall-clock deadline in `store.py`/`serial_link.py` uses `monotonic`/`perf_counter`, so a frozen `time.time` hangs nothing.
  - Mutation "`_unstorable_first` set on every drop" fails the test (`mutD.txt`: 1 failed), so first and last are genuinely distinguished.
- `_carried`: bounded at `CARRIED_MAX` with the oldest evicted. The carried tuple shares the `plot_names` set with the stopped port, whose consumer is cancelled first, so nothing mutates it afterwards.
- `cli_daemonctl.py` openers on Windows:
  - `_open_append`'s Windows branch is unchanged.
  - In `_replace_pid_record`, `os.open(..., 0o600)` keeps `_S_IWRITE` (0o200), so the temp file is not created read-only.
  - CPython's `FileIO` adds `O_BINARY|O_NOINHERIT` before calling the opener, so no CRLF and no inheritance change.
  - The POSIX-only mode tests skip there.
- Status "then restart": `test_a_move_aside_cause_says_then_restart` is revert-verified by fix3. `--json` is unaffected.
- Test runs, single files (`files.txt`):

  | Run | Result |
  |---|---|
  | `test_cli_last_ms_ceiling` | 10 passed |
  | `test_serial_link_rx_ingest` | 19 passed |
  | `test_cli_daemonctl_private_files` | 3 passed |
  | `test_cli_output_reserved_names` | 14 passed |
  | `test_cli_status_capture_health` | 8 passed |
  | `test_reconnect -k carried` | 2 passed |
  | ruff on the touched files | clean |

## Not covered

- Any Windows run, including FD-OUTER3-4, the V104 tests and the opener behaviour, which is reasoned from CPython source.
- The fix4-cli revert table (no ceiling, no `min`, earlier `--to`, ceiling from `time.time()`): taken from the report, not re-driven.
- The `_pin_ceiling` cost on `big.db`.
- Memory worst case of `_carried`: 256 aliases × 256 names × up to about 4 KB each is about 256 MB. It needs a client to attach 256 aliases; not driven.
- A small accounting nit in `plot_names`: two lines in a burst that share a new name, where the first fails to store, give the name back while the second stored it. The leak is bounded and only follows an announced failure (as fixdiff2-outer noted). Not re-examined for typed names.

## The two questions

1. Least confident: FD-OUTER3-4. Both the Windows 11 device-name rules and `os.stat("NUL")` are recalled, not driven. If `os.stat` raises on the owner's desktop, the name branch is needed and only its precedence over a successful stat is at issue.
2. Not thought about: whether any web UI or `/plot/export` path sends `since_ts` and `until_ts` derived from a `last_ms` the way the CLI now does. `plot export` passes `last_ms` through to the daemon, which I read; the web UI I did not.
