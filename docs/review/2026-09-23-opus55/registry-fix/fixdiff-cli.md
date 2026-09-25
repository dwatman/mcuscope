# Fix-diff review, partition cli (a597ae6)

Scope: `git show a597ae6 -- host/mcuscope/cli*.py host/tests/support.py` and the CLI tests, against `registry-fix/cli.md`, `registry-fix/cli-tests.md`, the triage batches and the owner rulings.
Probes and mutants ran in a private copy (`~/tt-data/mcuscope-2026-09-25/fixdiff-cli/copy`), one test file (or a named handful) per pytest process; `mutate.py` and `mutants.txt` there.

## Findings

### X-cli-1 MEDIUM (class 19, a sibling left out): `tail -f --match` accepts the patterns D-1 made the daemon refuse
- R18-1 copied the daemon's length cap and R19-2 its flags into `_follow_ws` (`cli.py:1216-1223`). D-1's repeat budget (`store.compile_user_regex`, `MAX_REPEAT_EXPANSION`) landed in the same commit and was not copied.
- Driven: `(?:(?:a{100}){100}){100}` (24 chars, expansion 1,010,100):
  - `store.compile_user_regex` refuses it;
  - `mcu tail -f -n 0 --match` with it compiles locally in 0.4 s and follows, with no refusal (probe over `test_cli_follow._tail_follow`).
- So `lines --match P` is exit 1 while `tail -f -n 0 --match P` runs. With `-n N`, the daemon refuses the backfill only after the client has already compiled the pattern.
- The memory half is reasoned from `store.py:437` (a 45-character pattern exhausts memory), not driven. The process that runs out is the client, on the daemon's host.
- Fix: run the same guard before the local compile, either duplicated like `MAX_MATCH_LEN` or moved to a module without the SQLite stack. Add a case beside `test_follow_match_over_the_daemons_length_cap_is_refused`.

### X-cli-2 LOW (class 86): the D-16 gate lets any pre-release through, on either side
- `cli_client.py:102` `check_daemon_version` reuses `update_check.is_newer`, written for the update notice. There, "unparsable answers False" is the quiet side; in a refusal gate it is the permissive side.
- Driven (`is_newer` directly):
  - `is_newer("0.6.0rc1", "0.5.0")` is False, so an `mcu 0.6.0rc1` accepts a released 0.5.0 daemon. That is the D-16 hole the ruling closes.
  - `is_newer("0.5.0", "0.5.0rc1")` is False, so a 0.5.0 `mcu` accepts its own pre-release daemon, which may lack fields added before the final release.
- On those paths only a missing header is refused. Current builds carry plain versions (`__version__ = "0.5.0"`, no rc published), so this is latent.
- SPEC 3.4 and SPEC 4 (`docs/SPEC.md:1168`) promise to refuse "older" with no exemption. Only the docstring names the dev-build pass.
- Fix: order pre-releases (or refuse an unorderable daemon version), and say in SPEC 4 what happens to one.

### X-cli-3 LOW (class 27): six doubles still replace `Client.open` and skip the new seam's check
- These patch `open` to a bare `httpx.Client`, so no version hook runs: `test_cli_send_verdicts.py:252,267`, `test_cli_can_dump.py:218`, `test_port_column_stored.py:157`, `test_cli_closed_output.py:34` and `test_scaffold.py:138`.
- `test_cli.py:720` `_FakeHttp` does the same.
- The cli-tests batch listed these (its Q2) and left them. The gap exists because the fix moved the check into `open`, and the cli batch itself edited `test_cli_send_verdicts.py`.
- Fix: route them through `support.canned`/`record_requests` (or `versioned`), as the batch did for the rest.

### X-cli-4 LOW: compatibility branches for older daemons remain on paths the D-16 check now covers
- D-16 and "no backcompat before 1.0" make these dead for any orderable version:
  - `cli.py:1240` bare-object `/ws` frame; `cli.py:1442` `wait` without `sends`; `cli.py:1666` a page without `name` ("before 0.3.0");
  - new in this fix: `cli.py:773`, `_absolute_window`'s `time.time()` fallback for a `/status` without `now`. Mutant M20 (fallback replaced by 0.0) survives the read-scope, export and port-health files.
- Not dead: `cli.py:187` and `cli_output.py:140`, since a current daemon sends those blocks as null.
- `test_cli_contract.py::test_wait_repeat_survives_a_daemon_without_the_send_counters` (R27-18) keeps the `sends` branch alive only through an unorderable header, which is X-cli-2's hole.
- Fix: delete the branches and the test that pins one, once X-cli-2 settles whether unorderable versions pass. That is an owner call if the pre-release route is meant to stay.

### X-cli-5 LOW (revert-verify): changed branches no test reaches
Mutants in the private copy, each run against the files that should pin it; all survived:
- M12: `cli.py:895`, dropping `s.port` from `_port_column`'s stream branch. `log export --decode -p a` (the paged path) with two boards would then print `[a]` on every row. The clause predates the fix, but the fix rewrote the line, and nothing pins it.
  - Fix: add a paged `-p` export case over two stored boards.
- M12b: dropping `s.json_out` from the same line. Redundant: `render` ignores `show_port` under `--json`. Delete the clause, or keep it and say why.
- M15: `cli_output.py:526`, `_ascii_type`'s "already ASCII" early return. Redundant: re-wrapping an `AsciiInt`/`AsciiIntRange` rebuilds the same type and range. Delete it.
- M2, M21, M24: the `not isinstance(..., bool)` guards at `cli.py:664`, `:772` and `:2327`. Each is one clause, but no test sends `true` as an id, a `now` or a `next_since_id`.
  - A `true` `next_since_id` would move the follow to `since_id=1`, which is harmless. A `true` `id` would continue an export from id 1, re-reading rows.
  - Fix: one parametrized case per guard, or drop them as over-defence.

### X-cli-6 LOW (nit, class 29): a stale comment and a filter that now hides an extra request
- `test_port_health.py:222-224`: the comment says a version probe precedes the query. The probe is gone, and `lines --to` now sends one request.
- `queries = [q for q in seen if q]` drops parameter-less requests before asserting "one query, no bound-resolving lookup". A stray `/status` (R44-1 added one for `--last-ms`) would pass unseen.
- Fix: assert on `seen` whole and drop the comment.

## Checked, nothing found

- Test files, run alone at HEAD in the copy, all pass:
  - the fix's own files: `test_cli_client_version` 11, `test_cli_follow` 29, `test_cli_numeric_grammar` 32, `test_cli_read_scope` 45, `test_cli_decode_dropped_points` 2, `test_cli_daemon_start_pid` 4, `test_cli_send_verdicts` 29;
  - the files it adapted: `test_cli_contract` 60, `test_cli` 167 (its `Stack` cases hit a real daemon, so the header check ran end to end), `test_cli_follow_frames` 11, `test_cli_small_refusals` 16, `test_port_health` 27, `test_cli_daemon_stop_scope` 21, `test_cli_daemonctl` 30, `test_cli_ux` 27, `test_cli_attach` 18, `test_cli_export_files` 33, `test_cli_export` 35.
- Mutants killed, each by the test the fix report names:
  - M1 `_iter_pages_asc` returning instead of dying;
  - M5 dropping the ws hostname check (`[http://]` case);
  - M16 dropping `decimal_float`'s `isfinite`;
  - M22 dropping the ws handshake check;
  - M23 dropping `probe_status`'s hook clearing (13 failures);
  - M25 dropping `launcher_pid`;
  - M26 dropping ` (with -p)`;
  - M27 reverting `_fetch_after` to the last row's id.
- D-16 as ruled: `DAEMON_MIN_VERSION = __version__`, and every per-command gate is gone.
  - No stale references remain in code, tests or docs (grep `require_daemon|older_daemon|_stream_port_column`). `docs/RELEASING.md:37` still reads true.
  - The daemon adds the header outermost, and in `_unhandled_error` for Starlette's 500. A header-less 500 probe gives "not an mcuscope daemon", but the real daemon cannot produce one.
- The `mcu daemon` exemption holds: every call in `cli_daemonctl.py` and in `daemon start/stop/status/restart` goes through `probe`/`probe_status`, which clear the hook.
  - `probe` elsewhere (`_stream_boards`, `attach`'s `/ports`) always precedes a checked call.
- The check is not swallowed by any guard. `die` raises `typer.Exit`, which is outside `_daemon_errors`' types and outside `_dump_follow`'s per-poll types. httpx closes the response when a hook raises, so a refused stream opens no file.
- R16-1: point names are unique within a definition (`protocol.py:762-773` refuses duplicates, lanes included). Bits points are named by lane, so the by-name lookup is exact.
- R16-3: a non-object row on a truncated page is refused by `_list_field` before `_highest_id` runs (driven: `log export --decode -o` and `lines --since-id 0`, both exit 1 "'lines' has non-object entries"), so `r.get` never meets a string.
- R18-1: no ≤200-character pattern raised `RecursionError` in probes (100-deep groups, 50-deep non-capturing groups, 199 `[`). The arm is defensive; its test stubs `regex.compile`.
- R19-2: `store.USER_REGEX_FLAGS` is exactly `regex.ASCII`, matching `cli.py:1219`.
- R20-2 against SPEC 3.4 `next_since_id` (same snapshot, never below `since_id`, capped by `id_to`):
  - `since = max([since, *covered])` runs only after a whole walk succeeds, so a mid-walk failure keeps the old watermark;
  - the capture-change reset runs after the max, so a new capture's low ids still reset it.
- R17-4: `pid` is `/status` `pid` only when `proc.pid` is in `_serving_pids`, since any other pid dies earlier as "another daemon is already serving". Class 82 holds.
- R22-2: the group walk finds every numeric param. An enumeration over `typer.main.get_command(cli.app)` found no Int/Float type outside `_AsciiNumber`.
  - The float options are `daemon start/restart --timeout`, `plot channels --active` and `purge --before-days`. SPEC 4 says the decimal point is "for a number of seconds", and `--before-days` is days: a one-word nit, noted, not a finding.
  - No other `int()`/`float()` on user text remains in `cli*.py`. `parse_clock` already uses `[0-9]`.
- R25-3: the port set grows only from rows that are printed (after the chan, match and decode filters), and port `""` is excluded. A failed `/ports` probe now starts an empty set that grows, where before the column stayed off.
- R44-1, R53, R54-1, R63-2, O-56a: implemented as the batch describes, and SPEC 4 and `AI_GUIDE` match the code (version refusal text, numeric grammar, `--last-ms` anchor, launcher pid, "not judged"). `test_cli_contract.py` passes.
- `support.py`: `_REAL_OPEN` is taken at import, before any patch, and `versioned` only sets a header a handler did not set, so the older-version cases still reach the check.

## The two questions

1. Least confident:
   - X-cli-1's memory half is reasoned, not driven. I did not run the 45-character pattern in the CLI, since it can freeze this 16 GB host.
   - I also did not run a real `mcu` against a real older daemon (a 0.4.0 wheel), only header-less and old-header doubles, plus `Stack` for the current one.
2. Not yet thought about:
   - Whether the web UI (`api.js`) applies the same version check or the repeat budget to pane regexes. That is the webui-fw partition.
   - A daemon behind a reverse proxy that strips `X-Mcuscope-Version` now fails every CLI command with "not an mcuscope daemon". Nothing documents that the header must survive a proxy.

Scratch not removed, because a recursive delete needs the owner's confirmation: `~/tt-data/mcuscope-2026-09-25/fixdiff-cli/` holds `copy/` (host, tools and docs copy, with `tests/test_probe_fixdiff.py`), `mutate.py`, `mutants.txt` and `d_cli.diff`/`d_other.diff`. No database or daemon was started.
