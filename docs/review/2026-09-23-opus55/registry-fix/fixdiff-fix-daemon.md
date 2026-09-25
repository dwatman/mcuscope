# Fix-diff fixes, partition daemon (on 01f7a66)

Findings: `registry-fix/fixdiff-daemon.md`. Rules: `registry-triage/fix-brief.md`.
Scratch (probes, `mutate.py`, `mutants.log`, `singles.log`): `~/tt-data/mcuscope-2026-09-25/fix-daemon-fixdiff/`.

Tally: 7 of 7 fixed (X-daemon-6 on the recommended option, owner to confirm); class 87 filed; 10 of 10 mutants caught.

## Findings

| Id | Status | Test that fails with the fix reverted |
|---|---|---|
| X-daemon-1 HIGH | fixed: the verbose/V1 branch of `repeat_expansion` counts every brace group `regex` reads as a count | `test_server_regex_budget.py::test_a_verbose_count_spelled_with_spaces_or_comments_is_bounded_on_every_route` (6 spellings x 4 routes), plus 4 new `test_repeat_expansion_counts_what_regex_expands` cases |
| X-daemon-2 MEDIUM | fixed in `compile_user_regex`: the flag-clash `ValueError` is raised as `regex.error` | `test_server_regex_budget.py::test_an_inline_flag_clashing_with_ascii_is_a_400_on_every_route` (`(?u)x`, `(?L)x`, `(?au)x` x 4 routes) |
| X-daemon-3 LOW | fixed: SPEC 3.4 `/marker` and the `MarkerBody` comment say U+0020 | wording only; `test_marker_text_is_stripped_and_a_blank_one_refused` already pins the behaviour |
| X-daemon-4 LOW | fixed: `save_ports` seeds from an inline `ports = [{...}]`, copying each inline table into a `[[ports]]` table | `test_config_writeback.py::test_an_inline_ports_array_keeps_its_unknown_keys` |
| X-daemon-5 LOW | fixed: `_url_grammar` compiles with `re.ASCII` | `test_server_request_validation.py::test_query_and_path_params_hold_their_grammar` (`ınf`, `İnf` cases) |
| X-daemon-6 LOW | fixed on the recommended option: `resolve_db_path` returns `:memory:` unchanged; SPEC 3.3 names it | `test_config_db_path.py::test_an_in_memory_db_path_is_not_taken_as_a_file_name` |
| X-daemon-7 LOW | fixed: the export test waits for the failure to be posted to the loop before cancelling | `test_server_raced_futures.py::test_an_export_build_that_failed_as_its_handler_was_cancelled_is_retrieved` |

Details that matter to a reviewer:

- X-daemon-1: the scan mirrors `regex._regex_core.Source` (read, not guessed).
  - Inside a verbose count `regex` skips any `str.isspace()` character (so U+00A0 and U+0085 too, even under `regex.ASCII`) and a `#` comment up to `\n`, which may contain `}`: `{1#}\n00}` is 100. Digits are ASCII only.
  - Python's `\s` equals `str.isspace()` over all code points (checked by enumeration), so `_VERBOSE_COUNT` uses it.
  - Stripping `#...` from the whole pattern before `_COUNT`, the finding's second option, undercounts `(?x)[#](?:a{100}){100}`: the `#` in the set would eat the counts. Not taken.
  - V1 without `x` reads a spaced count as literals; the scan counts it anyway. It stays an upper bound.
- X-daemon-2: fixed once in `compile_user_regex`, so the server sites, the REGEXP callback and the CLI's coming `tail -f --match` get it without a per-site guard.
  - The 400 text is `bad match regex: ASCII, LOCALE and UNICODE flags are mutually incompatible (user patterns always compile as ASCII)` (`/assert`: `bad regex '<p>': ...`).
  - `RecursionError`, which the CLI catches, is not reachable within `MAX_MATCH_LEN` 200: 100 nested groups compile in a worker thread (probed).
- X-daemon-4: tomlkit renders the replacing `[[ports]]` in place and moves a top-level key that followed the inline array above it (probed, and asserted in the test: `top = 2` is not swallowed by the last table).
- X-daemon-7: the rewrite first survived the mutant it exists to catch (removing `built.cancel()`).
  - Cause: the test's own list held the concurrent future, whose done callbacks are kept after they run and reach the handler's future, so that future outlived the recording and its failure was never reported. The test now drops every holder inside the case.
  - Simulated load (a build that takes 0.5 s to fail after release), under the mutant: the original test passes (vacuous), the new one fails. Unloaded, both catch the mutant 5 of 5.

## `compile_user_regex` contract (for the CLI agent)

Signature unchanged: `compile_user_regex(pattern: str) -> regex.Pattern[str]`.
Behaviour change: an inline `(?u)`, `(?L)` or `(?au)` now raises `regex.error`, not `ValueError`.
It raises only `PatternTooLarge` (a `ValueError` subclass) or `regex.error`; catch `PatternTooLarge` first.
Callers still check `MAX_MATCH_LEN` themselves.

## Revert-verify

`mutate.py` on a private copy of `host/` and `tools/`, one test file per mutant, `-p no:randomly`; the raced-futures mutant 5 runs. Every mutant caught (`mutants.log`):

1. verbose branch back to `_COUNT.finditer`
2. no `#` comment alternative inside a verbose count
3. ASCII-only whitespace inside a verbose count
4. ignored text not stripped before `_COUNT`
5. `ValueError` not mapped to `regex.error`
6. URL grammar without `re.ASCII`
7. `:memory:` joined onto `base_dir`
8. inline `ports` not seeded
9. inline table appended without copying into a table
10. `built.cancel()` removed from `_build_admitted`'s cancel path

## Class 87 sweep verdicts (current tree)

Filed in `docs/REVIEW.md` as class 87 (Invariant, Bit, Sweep; the suggested shared header list is left out as a hardening idea, not a sweep).
4 `add_middleware` sites (`server.py:633-636`), 2 amenders, 3 refusals outside the app, plus uvicorn's own errors.

| Site | Kind | Verdict |
|---|---|---|
| `_VersionHeader` (`server.py:935`) | amends `http.response.start`, `websocket.accept` | complies: `_unhandled_error` (`server.py:628`) adds `_VERSION_HEADERS`; pinned by `test_server_version_header.py::test_an_unhandled_error_carries_it` |
| `_FrameDenial` (`server.py:952`) | amends `http.response.start` | complies: `_unhandled_error` adds `_NO_FRAMING`; same test asserts `x-frame-options` |
| `_TokenGuard` (`server.py:777`) | sends its own refusals | exempt: added before both amenders, so it runs inside them and its refusals carry both |
| `_SameOriginGuard` (`server.py:713`) | sends its own refusals | exempt: same |
| WS closed before accept (`server.py:758, 888, 906`) | uvicorn renders `websocket.close` as a bare 403 | exempt: not a page (SPEC 3.1), and SPEC 3.4 promises the header on the accept, which a refusal is not |
| uvicorn's own protocol errors | server-level | exempt: no app hook exists |

## CHANGELOG lines

- A `/lines`, `/lines/export`, `/wait` or `/assert` pattern whose inline `(?u)` or `(?L)` clashes with the ASCII dialect is a 400 `bad match regex: ...`, not a 500.
- A verbose (`(?x)`) pattern can no longer pass the regex size bound by writing a count with spaces or a comment inside its braces (`{ 100 }`).
- `PUT /config/ports` keeps unknown keys of a port written as an inline `ports = [{...}]` array (rewritten as `[[ports]]` tables; comments inside it are lost).
- `storage.db_path = ":memory:"` opens an in-memory capture again, instead of a file named `:memory:` beside the config.
- A URL number spelled `ınf` or `İnf` gets the grammar's own 422 text.

## Needs another batch

- cli (`cli.py:1219`, `_follow_ws`): replace `regex.compile(match, flags=regex.ASCII)` with `store.compile_user_regex(match)` (after the `MAX_MATCH_LEN` check), catching `PatternTooLarge` then `regex.error`. That closes both the `(?u)` crash and the missing expansion bound the finding hands over.
- daemon.py / lockfile.py: with `db_path = ":memory:"`, `CaptureLock` takes `:memory:.lock` in the working directory, an illegal file name on Windows.
  This predates a597ae6: the lock is pointless for an in-memory capture, so skip it there (or refuse `:memory:` at load, the other option of X-daemon-6).
- CHANGELOG (orchestrator): the lines above.
- `registry-fix/daemon-api.md` R19-3 still says markers "share `_stripped`"; it is a past report, left as written.

## Owner to confirm

- X-daemon-6: implemented the recommended pass-through, which restores the pre-a597ae6 behaviour. The alternative is to refuse `:memory:` at load and leave the `Store` branches reachable only from tests. The `CaptureLock` item above weighs on that choice.

## Needs Windows

- `resolve_db_path` pass-through and the `:memory:.lock` name (above).
- Nothing else here is platform-specific.

## Needs a browser

None.

## The two questions

1. Least confident: that the verbose scan is now an upper bound for every count `regex` accepts.
   - Checked against `regex`'s parser source (`Source.get`, `parse_limited_quantifier`, `DIGITS`) and by driving 16 spellings under `(?x)`, `(?V1)`, `(?xV1)`, including a comment holding `}`, `\r`, and Unicode spaces.
   - Residual: a future `regex` release that widens what a count admits. The comment on `_VERBOSE_COUNT` names the source function to re-read.
2. What we had not thought about: that a test's own bookkeeping can keep alive the object whose collection is the observation point.
   - X-daemon-7's first rewrite passed with the fix reverted because the list that captured the concurrent future kept the handler's future alive. Only the mutant run showed it; this is class 78's missing positive control, reached through a reference path.

## Verified, and how

- Every mutant above caught (`mutate.py`, `mutants.log`).
- Single files on the real tree, one at a time: the 54 non-CLI test files that name a changed function, route or `db_path` (`singles.log`), 1031 passed, none failed or skipped.
- `uv run python -m ruff check` on the three modules and five test files: clean. No U+2013 or U+2014 in any touched file (grep -P).

## Cleanup owed

- `~/tt-data/mcuscope-2026-09-25/fix-daemon-fixdiff/copy/` (6.4 MB, rsync of `host/` and `tools/` without `.venv`, databases or caches) needs a recursive delete, which needs the owner's confirmation.
- Kept to rerun: the probes, `mutate.py`, `dbg_test.py`, the original `test_server_raced_futures.py` (for the slow-build comparison) and the logs.

## Not verified

- Windows (above). No whole suite, no `mcuscoped` process (in-process `TestClient` only). The CLI side of X-daemon-2 is not changed or run.
