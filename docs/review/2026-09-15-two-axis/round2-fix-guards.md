# Round 2 fix: export guard double

Files edited:

- `host/tests/webui_js/exportdlg_guards.mjs`
- `host/tests/test_webui_js.py`
- `host/tests/webui_js/exportdlg.test.mjs` (comments only)

## Guards mirrored

The double runs in the daemon's order.
First comes the FastAPI 422 pass: all errors, in declaration order, joined with "; ", and each input printed as a Python `repr`.
After that come each handler's 400 guards.

- 422 parsing follows what pydantic actually does, found by probing the real app. There are four parsers:
  - int: trims Unicode whitespace, allows a sign, single underscores and a `.000` tail, compared as BigInt.
  - float: no leading, trailing or doubled underscore, then Rust's f64 grammar (so `inf` and `nan` parse).
  - bool: the 12 case-insensitive spellings, no whitespace allowed.
  - `chan`: a Literal list, each bad value reported as `chan.N`.
- A repeated scalar parameter takes its last value, as Starlette does.
- `/lines/export`: 422 (chan, since_id, since_ts, until_ts, last_ms, id_to), then format, then match length (counted in code points), then window, then session.
- `/can/frames`: 422 (bus, last_ms, since_ts, until_ts, since_id, id_to, limit), then format, then window, then the id list (`empty can id in list`, `bad can id`, `can id out of range`), then session.
- `/plot/export`: 422 (names required, last_ms, since_ts, until_ts, id_to, decode, changes), then these 400 guards in order:
  - `names is required`, then format, then window.
  - `changes requires decode`, then `deadband requires changes`.
  - `_parse_deadband`, including `value is not a number`: an ASCII, finite Python float literal.
  - session, then unknown channel names, scoped by a non-empty `port`.
- Stored state comes from `refuse(url, known)`, where `known = {channels: [{name, port}] | null, sessions: [{id, name}] | null}`.
  - Sessions resolve by a decimal id of at most 20 digits, or else by name.
  - `null` skips that guard.
  - `installExportDaemon(env, sessions = null, channels = null)` passes on what it is given. All four importers still pass (48 tests).

## Excluded, and why

These are named in one comment in the double and one in `GUARD_URLS`:

- Regex compile errors in `match`: the Python `regex` syntax cannot be reproduced in JS.
- The wide-export one-stream check and the deadband-on-a-label check: both need decoder or stream state the double does not model.
- Non-finite `since_ts`/`until_ts` on the export paths: the daemon answered **500** (see Contradictions); fixed in the same round, and now mirrored and in the contract.

## Contract test

- 183 URLs, all distinct. Every mirrored clause has at least one refused and one accepted URL, covering:
  - every URL from the brief;
  - the boundaries it asked for: `id_to` 0 and -1, `last_ms` at 10^15 and one over, `until_ts == since_ts`, can id 0x1FFFFFFF and 0x20000000, empty list elements, repeated `chan`;
  - guard-order pairs (two faults, where the first guard's message must win).
- The fixture now starts one session, `run-a`, which gets id 2 because the auto session holds id 1; the test asserts this.
- The known state is built from the store's own session list plus channel `v` on port `board`.
- Node skip behaviour is unchanged.

## Mutation results

The file was restored from `r2fix_guards.final.mjs` after each run, and `cmp` confirmed the restore.

- M1, deadband number check removed: FAIL (caught).
- M2, `empty can id in list` message changed: FAIL (caught).
- M3, session guard removed: FAIL (caught).
- M4, `le` bound off by one: FAIL (caught).
- M5, repeated scalar takes the first value: FAIL (caught).

## Contradictions and findings

- **Daemon defect (not fixed, outside my files).** A non-finite time bound gives a 500 instead of a 400 or 422:
  - Affected: `/lines/export?since_ts=inf`, `/plot/export?names=v&since_ts=inf`, and `/can/frames?since_ts=inf&format=csv`.
  - `inf` (also `infinity`, `-inf`, `1e400`) answers "timestamp out of range for platform time_t", raised from `export_filename` via `time.localtime`.
  - `nan` answers "Invalid value NaN (not a number)".
  - pydantic accepts both values, and `_check_window` does not refuse them (NaN comparisons are false).
  - The JSON path of `/can/frames` answers 200.
  - This fits REVIEW class 22 ("parsing is not validation").
- **The brief's "no sessions" fixture option does not hold as stated:** the daemon creates an auto session at id 1, so `session=1` resolves.
- **Unrelated failure:** `test_webui_js_suite` failed in `can_head.test.mjs:247`, a column tooltip text mismatch.
  - It came from the parallel CAN age edit and was fixed there; the double's importers passed throughout.
- `ruff check tests/test_webui_js.py` is clean.
