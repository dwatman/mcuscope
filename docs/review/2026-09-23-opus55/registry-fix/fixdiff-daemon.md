# Fix-diff review, partition daemon (a597ae6)

Scope: `git show a597ae6 --` the twelve daemon modules (2077 diff lines, read in full with their surrounding code), SPEC 2, 3, 7 hunks, and the tests that pin them.
Inputs: `registry-triage/batch-daemon-{api,process}.md`, `batch-tests-daemon.md`, `decisions.md` (rulings), `registry-fix/{daemon-api,daemon-process,tests-daemon}.md`.
Scratch (probes, `mutate.py`, `mutants.log`, `singles.log`): `~/tt-data/mcuscope-2026-09-25/fixdiff-daemon/`.

Tally: 1 HIGH, 1 MEDIUM, 5 LOW; draft class 87 below; revert-verify sample 32 of 32 caught.

## Findings

### X-daemon-1 HIGH: a verbose pattern with spaces in its braces bypasses the expansion bound (R1-1 reopened)
- `store.py:1691-1695` (`repeat_expansion`, verbose/V1 branch): the coarse bound multiplies only what `_COUNT` (`\{(\d*)(,?)(\d*)\}`) matches, and in verbose mode `regex` reads `{ 100 }`, `{1 0 0}`, `{100 ,}` and `{ 1#c\n00 }` as the count 100.
- Driven (`probe_verbose.py`, `probe_gil.py`, `probe_api.py`):
  - `(?x)(?:(?:a{ 100 }){ 100 }){ 100 }` (34 chars) scans as 34, is accepted by `/lines`, and compiles in 0.37 s with a 5 ms heartbeat thread stalled 363 ms (the compile holds the GIL, so `to_thread` does not shield the loop).
  - `(?x)(?:(?:(?:a{ 100 }){ 100 }){ 100 }){ 100 }` (45 chars) scans as 45 and raises `MemoryError` under a 1.5 GB `ulimit -v`: the exact memory exhaustion D-1 A exists to close.
- Fix: in the verbose/V1 branch, count every brace group whose content is digits, commas, whitespace and comments (or strip whitespace and `#...\n` from the pattern before `_COUNT`); add the four spellings above to `test_server_regex_budget.py`.

### X-daemon-2 MEDIUM: `(?u)` and `(?L)` in a user pattern are now a 500
- `store.py:1781` compiles with `regex.ASCII`; `regex` then raises `ValueError: ASCII, LOCALE and UNICODE flags are mutually incompatible` for an inline `(?u)` or `(?L)`.
- `server.py` `_compile_match` and `_compile_patterns` catch only `PatternTooLarge` and `regex.error`, so the ValueError reaches `_unhandled_error`.
- Driven (`probe_api.py`): `/lines`, `/lines/export`, `/wait`, `/assert` all answer 500 for `(?u)x` and `(?L)x`. Before a597ae6 both compiled (200); `(?au)x` was already a 500 (class 18).
- Fix: catch `ValueError` beside `regex.error` (after `PatternTooLarge`, its subclass) at both sites, answering the 400 `bad match regex: ...` / `bad regex ...`; a test per route.
- Sibling in the cli partition (class 19, the mirror copied the flag without the guard): `cli.py:1219` `_follow_ws` compiles `regex.compile(match, flags=regex.ASCII)` catching `regex.error` and `RecursionError` only.
  - Driven: `mcu --url http://127.0.0.1:1 tail -f --match '(?u)x'` (scratch `MCUSCOPE_*_DIR`) prints a traceback and writes `mcu-crash.log`, exit 1.
  - The same site also skips the expansion bound, so `tail -f --match` with X-daemon-1's pattern costs the CLI what it would have cost the daemon. Handing both to the cli fix-diff.

### X-daemon-7 LOW: the export half of `test_server_raced_futures.py` passes vacuously under load
- `tests/test_server_raced_futures.py:88`: `time.sleep(0.3)` stands for "the build fails and posts its result" before `task.cancel()`; under load the worker may not have failed yet, the cancel then hits a running build, and `assert ... == []` holds either way (class 21's assumption, class 78's missing positive control).
- Reasoned, not driven (a loaded machine is needed to show it).
- Fix: have `build` record that it raised, then block the loop until the concurrent future is done (for example keep the `cf` via a patched `_pool(...).submit` and spin `while not cf.done(): time.sleep(0.01)`), and assert that `built` was done when cancelled.

### X-daemon-3 LOW: SPEC 3.4 `/marker` says `str.strip`; the code strips U+0020 only
- `docs/SPEC.md:866`: "stored stripped of surrounding whitespace (Python's `str.strip`, as a session name is)".
- `server.py:345-360`: `MarkerBody.text` uses `_space_stripped` (breaks folded, then `strip(" ")`), per D-9; `test_marker_text_is_stripped_and_a_blank_one_refused` pins `"\tx\t"` stored as typed; CHANGELOG and SPEC 2.5 say the same.
- So SPEC 3.4 contradicts SPEC 2.5, the code, the test and the CHANGELOG. The `MarkerBody` comment ("as SessionBody.name is") and `registry-fix/daemon-api.md` R19-3 ("share `_stripped`") carry the same stale wording.
- Fix: SPEC 3.4 reads "stored stripped of surrounding spaces (U+0020, 2.5) after line breaks fold to spaces; a text that is then empty is a 422 `must not be blank`"; the comment says U+0020.

### X-daemon-4 LOW: `save_ports` still drops unknown keys from an inline `ports = [...]` array
- `config.py:684-686`: a `ports` value that is not a tomlkit `AoT` is replaced by a fresh array of tables; the comment calls it "a hand-made shape the loader refuses".
- The loader accepts it: `_check_shape` wants a list of dicts, which `ports = [{alias = "a", device = "/dev/x", future = 1}]` unwraps to.
- Driven (`probe_ports.py`): `read_config` loads that file (warning on `future`), and `save_ports` writes `[[ports]]` tables without `future = 1`. That is R45-1's defect, and SPEC 3.3's new "its unknown keys and comments survive", for that form.
- Fix: when `ports` is an inline array of inline tables, seed `saved` from its entries (copying each into a `tomlkit.table()` keeps the keys; inline comments cannot survive, which SPEC 3.3 can say), and correct the comment. Test: the inline file above keeps `future`.
- Split AoTs (`[[ports]]` a, `[storage]`, `[[ports]]` b) and a file with no ports were driven too and reload correctly.

### X-daemon-5 LOW: the URL float grammar's `(?i:...)` admits `ınf` and `İnf` (class 22 in the fix itself)
- `server.py:215` compiles every URL grammar without `re.ASCII`; the `UrlFloat` pattern's `(?i:-?(?:inf|infinity|nan))` then folds U+0131 and U+0130 to `i`.
- Driven: `GET /lines?since_ts=ınf` passes the grammar and answers pydantic's 422 `Input should be a valid number, unable to parse string as a number`, not `since_ts: must be an ASCII decimal number`. Still refused, but by the text the grammar exists to replace, and the grammar's own claim ("ASCII decimal") is false.
- Fix: `re.compile(pattern, re.ASCII)` in `_url_grammar` (or spell the words with `[iI]` classes); a test that `ınf` gets the grammar text.

### X-daemon-6 LOW: a config `db_path = ":memory:"` now opens a file named `:memory:` beside the config
- `config.py:162`: `resolve_db_path` joins every non-empty `db_path` onto `base_dir` and absolutises it; before a597ae6 `":memory:"` passed through `expanduser` unchanged and SQLite opened an in-memory capture.
- The daemon still special-cases `":memory:"` in `_export_dir` and six `Store` sites; from a config they are now unreachable, and on Windows `C:\...\:memory:` is not a legal file name.
- The value is undocumented (SPEC 3.3 never names it), so this is a choice, not an obvious bug. Either pass `":memory:"` through unchanged in `resolve_db_path` (then those branches keep a config route), or refuse it at load and delete nothing (the `Store` tests reach the branches directly). Owner pick; recommended: pass it through, one line.

## Checked, nothing found

Each driven unless marked reasoned.

- Owner rulings, daemon side.
  - D-1: bound plus off-loop compile at all four sites and `_make_regexp`. Only X-daemon-1's verbose spellings escape it. Group calls (`(?1)`, `(?&n)`) are not inlined: 40,000 plus ten calls compiled in 364 ms / 10.6 MB. Nesting 100 groups deep compiles in a worker thread.
  - D-3: `resolve_db_path` callers (`daemon.py:195, 432`, `server.py:470, 520, 1142, 1323, 1415, 3057`) all take a `Config` from `read_config`, `dataclasses.replace`, or `PUT /config/storage`'s `saved_view` with `base_dir`.
  - D-5: `USER_REGEX_FLAGS` is on every daemon compile.
  - D-6 and D-9: `format_command`, `format_marker` (blank and sigil checks) and `parse_marker` use U+0020 only. `MarkerBody` uses U+0020 only too, apart from SPEC wording (X-daemon-3). `format_response_ok/err` still `str.strip` their data; D-9 does not name them.
  - D-8: grammar edges driven on `/lines`: `+2`, ` 2`, `1_0`, `٣`, 21-digit, ` 1`, `1e99999` are 422; `nan` is the 400; `-0` since_id and `1e5` pass. `check=yes` is 422. `/ws` takes no numeric parameter; `parse_qs` at `server.py:856` reads only the token.
  - D-13: the sim programs no filter. D-14: the flood re-anchors. D-16 (daemon half): every HTTP response and the WS accept carry the header, the 500 included.
  - Owner picks: slot before sweep lock, `check=1`, "interrupted" at open, `last_ms` "by id".
- Retention watermark (`store.py:3236-3256`, reasoned over every transition):
  - floor absent to present and back, floor lowered, `retention_days` raised or lowered;
  - a late old row landing between chunks (`since=None` walks re-query from the start), and when `since` is set (reset, nothing recorded);
  - `INSERT INTO lines` has two sites, both behind the writer's `_check_stamp_order`.
- `/can/frames` `next_since_id` on a truncated page: the CLI pages down `id_to` before advancing (`cli.py:2307-2338`), and `since=0` stays one page by design.
- `_run_export` slot accounting: a cancel while waiting for the lock or in `prepare`, a `prepare` refusal, and a build failure each release the slot once.
- `_TempFileResponse`'s `await` in `finally` under cancellation: the lifespan's sweep of `export_files` still removes the file (`server.py:576-581`).
- `OverflowEpisode` state in `SimSource` is reached only under `SourceLink._lock`. `serve_pty`'s `write_lines` reads the rebound `sim` on each call.
- `save_ports`: split AoT and a file with no ports (`probe_ports.py`). Duplicate aliases in a body are refused 400 before `save_ports` (`server.py:1497`).
- Class 35: no bare `print(` is left in the daemon modules (grep).
- Class 20: `first_export_line_id` has an early return for empty `names` and keeps every `_export_where` term. `_delete_expired_chunk`'s `ts >= ? AND ts < ?` is a range on `idx_lines_ts`.
- Revert-verify (`mutate.py` on a private copy, single files, `-p no:randomly`): 32 mutants over every changed branch class, all caught (`mutants.log`).
  - The batches' reports claim 68 + 31 + 26. This sample re-checks them independently, it does not repeat them.
- Single files green on the real tree (`singles.log`): the 22 files touched or pinned by the diff in this partition, `test_e2e.py`, `test_plotjuggler.py` and `test_server_exports.py` included, whose pins the batch reports listed as red.

## Draft registry class 87

### 87. A response header added by middleware misses the 500 Starlette sends outside all middleware
- Invariant: every header an app middleware adds to each response is also on the 500 that `_unhandled_error` returns. Starlette's `ServerErrorMiddleware` sends that 500 through the server's own `send`, outside every `add_middleware` layer.
- Bit: 2026-09-25 (registry fix, daemon-api). `_FrameDenial` (SPEC 3.1, "every HTTP response") had never reached an unhandled error's 500. The new `_VersionHeader` would have missed it too, and the CLI would then read a daemon fault as "not an mcuscope daemon".
- Sweep: `grep -n "add_middleware\|http.response.start\|websocket.accept" host/mcuscope/server.py`. For every middleware that amends a message rather than sending its own, check that `_unhandled_error` adds the same header set, and that a test raises in a route and asserts that set on the 500.
  - Also list what the server itself answers outside the app (uvicorn's malformed-request 400, a WS handshake refused by `websocket.close` before accept, which uvicorn turns into a 403). Mark each exempt or covered.
  - Suggested hardening: one list of always-on headers that both the middleware and `_unhandled_error` read, so a third amender cannot be added to one and not the other.

Sweep run on a597ae6: 4 `add_middleware` sites, 2 amenders, and 3 responses outside the app.

| Site | Kind | Verdict |
|---|---|---|
| `_VersionHeader` (`server.py:934`) | amends `http.response.start`, `websocket.accept` | complies: `_unhandled_error` adds `_VERSION_HEADERS` (mutant caught) |
| `_FrameDenial` (`server.py:951`) | amends `http.response.start` | complies: `_unhandled_error` adds `_NO_FRAMING` (mutant caught) |
| `_TokenGuard` | sends its own refusals | exempt: inside both amenders, so its 401/403/429 carry both |
| `_SameOriginGuard` | sends its own refusals | exempt: same |
| WS refused before accept (`server.py:757, 887, 905`) | uvicorn renders `websocket.close` as a bare 403 | exempt for 3.1 (not a page). SPEC 3.4 promises the header on "the `/ws` accept", which a refusal is not; the CLI reads a refused handshake as its own error path (cli partition) |
| uvicorn's own protocol errors | server-level | exempt: no app hook exists |

## The two questions

1. Least confident: that `repeat_expansion` is an upper bound for every pattern `regex` accepts.
   - Rechecked by driving, not reading. This produced X-daemon-1: verbose mode admits whitespace and comments inside a count, which the coarse bound's `_COUNT` cannot see.
   - Probed and found safe in the non-verbose branch:
     - spaced braces there are literal (1 ms);
     - `{0,1000}` and `{1,1000}` nested three deep (1 ms);
     - group calls and `(?#...)` before a quantifier;
     - costly atoms at the bound: `(?fi)(?:ß{300}){300}` 137 ms and a class union 31 ms of heartbeat stall.
   - Residual: the bound limits memory, not the GIL. Up to ~0.26 s of loop stall inside the bound (`\X`) stands as the batch's open owner call.
2. What we had not thought about: the new flags' interaction with what the user can write inline.
   - Passing `regex.ASCII` made `(?u)` and `(?L)`, harmless before, into a `ValueError` no handler maps (X-daemon-2), in the daemon and in the CLI's mirror.
   - Working outward from each change's consumers also found:
     - SPEC 3.4 contradicting D-9 for markers (X-daemon-3);
     - the inline-array form `save_ports` still flattens (X-daemon-4);
     - the new grammar's own case-folding hole (X-daemon-5);
     - `:memory:` losing its meaning under `base_dir` (X-daemon-6).

## Not verified

- Windows: `_BREAK_ERRORS` without `termios`, `_emit`'s dup2 on a Windows pipe, a drive-relative `db_path` (`D:cap.db`), and whether `C:\...\:memory:` fails to open (X-daemon-6).
- X-daemon-7 is reasoned; no loaded-machine run.
- No whole suite and no `mcuscoped` process were run (in-process `TestClient` only).

## Cleanup owed

- `~/tt-data/mcuscope-2026-09-25/fixdiff-daemon/copy/` (13 MB, rsync of `host/`, `tools/`, `firmware/` without `.venv` or databases) needs a recursive delete, which needs the owner's confirmation.
- Probe databases and configs were deleted file by file. The probes, `mutate.py` and the logs are kept to rerun.
