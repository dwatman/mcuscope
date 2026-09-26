# Fix-diff review 3: the start id (uncommitted tree on 89a6af3)

HEAD 89a6af3ec4b19ca798ab384a5f2005936d88ddf2. Target: `git diff` plus untracked files, round 2 of `windows-fixbatch-race.md`.
The ours-or-lost decision is sound in every case checked. One MEDIUM on the pid record, which the new class 7 text calls safe.

## Findings

### G1 MEDIUM: a race on a non-loopback URL leaves the winner with no pid record, and `daemon stop` cannot stop it
- Where:
  - `cli_daemonctl.py:243` `_write_pid_record` checks and then replaces, so two starts can both read "no record" and both write.
  - `pidfile.claim` leaves a live foreign record alone.
  - `cli_daemonctl.py:299` the loser then removes the record naming its child.
  - `server.py:1203` `/shutdown` is loopback-only.
- Live, port 18595, two `mcu --token tokA|tokB --url http://100.98.9.94:18595 daemon start --sim` at once (`smoke.py lanrace`):
  - the verdicts were right 7 of 7: one `started`, one `another daemon is already serving at <url>; stopped this start's own process (pid N)`;
  - neither start printed the "already names a running process" warning, and 3 s later the data dir held no `.pid` in 6 of 7 rounds;
  - `mcu --token <winner's> daemon stop` then exited 1 all 6 times: `did not stop on a shutdown request, and no local pid record names it, so no process was signalled; stop it where it runs`. The daemon kept running on this machine, and only a manual kill stopped it.
  - On 127.0.0.1, the same race left no record in 2 of 3 rounds. There `stop` still exits 0 through `/shutdown` (`no local pid record: asked it to shut down`).
- Why this diff:
  - Round 2 turns the LAN refusal race into a reap. At HEAD both starts reported `started`.
  - The new class 7 "accepted cell" (`REVIEW.md:173`) and `windows.md:96` say `daemon stop` still stops such a winner through `/shutdown`. That holds only on a loopback bind.
- Fix (owner pick; recommended first):
  - On success, the winning CLI makes the record name its own child, overwriting a record that names another live pid.
    - The id match proves the answer is this start's own daemon, so class 82's objection (a `/status` pid may be a peer's) does not apply.
    - A loser's later `_write_pid_record` then refuses, and its `_remove_pid_record` skips.
  - Or the daemon re-claims its record once the live pid that made `claim` back off has exited. This closes every ordering, at the cost of a watcher in `pidfile`/`daemon`.
  - Either way, correct the class 7 cell and the `windows.md` line.
  - Add a test: two starts both reading "no record", the loser's child named last, then the winner's record must survive the reap.
- Fixed: once its answer carries the start id, the winning CLI rewrites the record to name its own child (`cli_daemonctl.py` `_record_own_daemon`, called in `_await_spawned`), with a stderr note when it replaces another pid. A losing start never writes over a live pid and removes only its own child's record, so the reap cannot undo it in either order. Class 7 and `windows.md` are corrected. Tests: the loser's record landing last, then its reap; the reap first. Live: 7 of 7 LAN token races on 100.98.9.94:18594, `daemon stop` exit 0 each time.

### G2 NIT: `answered_id` can be unbound
- `cli.py:2722` initialises `body` and `refusal`, but not `answered_id`.
- A `--timeout 0` start breaks out of the loop at the deadline branch before any probe, and so reaches `cli.py:2764` without it.
- Today that is safe only because `_abandon_daemon` never returns.
- Fix: `answered_id: str | None = None` beside the other two.
- Fixed: `answered_id: str | None = None`. Its removal is an equivalent mutant while `_abandon_daemon` never returns.

### G3 NIT: a Ctrl-C between `Popen` returning and the `try` leaves the child unnamed
- `cli.py:2684-2689`: the `finally: err_fh.close()` runs outside the handler. The window is microseconds.
- Fix: open the `try` straight after the spawn, or close `err_fh` inside `_await_spawned`'s `try`.
- Fixed: one handler from the spawn on. Popen and the `err_fh.close()` sit inside it, and `proc is None` (Ctrl-C inside Popen) re-raises. A Ctrl-C inside Popen after the child exists is CPython's window and cannot be caught here. Tests: a Ctrl-C at the close, and one inside Popen.

### G4 NIT: the id tells an unauthenticated LAN client when the daemon restarts
- The id changes with each start, and it rides on 401s, so a client with no token can notice every restart. The version header does not reveal that.
- Nothing accepts the id as input, and it cannot be learned before the child serves, so it cannot be forged into a false "ours".
- If the owner wants it closed: echo the id only when `client.host` is loopback or equals the bound address. The CLI's own LAN probe arrives from the bound address. I would leave it as it is.

## Checked and sound
- An answer without the header is lost: an older daemon, a foreign server, a proxy that strips it.
  - Only a status-shaped body or a 401/403/429 `{"error": str}` stops the wait. Anything else keeps polling until the deadline or the child's exit, including uvicorn's own plain-text 400/500/503, and a 500 or non-JSON reply from any server.
- Startup ordering: uvicorn runs the lifespan startup before `create_server` (`uvicorn/server.py:106-152`), and the header list is fixed in `create_app`. The child cannot answer without its id.
- Middleware order: `_VersionHeader` is outermost, so Host/Origin 403, token 401/429, 404 and 422 all carry the header. `_unhandled_error` adds it to 500s.
- A WebSocket refused before accept gets uvicorn's bare 403 with neither header. The CLI never probes `/ws`, and SPEC names only "the `/ws` accept".
- Env leakage: `start` always overwrites `MCUSCOPED_START_ID` with a fresh `token_hex(16)`, so an inherited or exported value never matches.
  - A foreground `mcuscoped` with a leaked valid id just echoes it, and a bad one gets the startup note.
  - `mcuscoped --sim`, systemd and `support.py` send no id, which is correct: `stop`/`status` ignore it.
  - `restart` goes through `_start_daemon`. It is the only spawn site.
- Exits and messages match SPEC 4, the AI guide and class 89. On Ctrl-C a live child is named with its record kept, and an exited child's record is removed.
- CI step (`ci.yml:116`): the report exists (the `Test` step tees `pytest-report.txt` in `host/` with `-rs` under bash pipefail).
  - I ran real pytest 9.1.1 with `PY_COLORS=1` and `--cov`. `SKIPPED.*launcher` matched all three launcher reasons, with ANSI colour around `SKIPPED`, and did not match an unrelated skip.
  - No other test's skip reason contains "launcher". It catches a skip, not a renamed or deselected test, like the other guards.
- No U+2013/U+2014 in any changed or new file. `ruff check .` is clean, and `test_cli_contract.py` passes (59).

## Revert table
Scratch `~/tt-data/mcuscope-2026-09-26/fixdiff3/host`, runner `mutate.py`:
- every anchor is checked before any write, and each restore is asserted;
- runs use `PYTHONDONTWRITEBYTECODE=1`, `PYTHONPATH=<scratch>`, `-p no:cacheprovider -p no:randomly`, one pytest process per file;
- baseline: stop_scope 38 passed and 1 skipped, version_header 6, start_id 8, daemonctl 30.

| Mutant | Result | Caught by |
|---|---|---|
| V1 any echoed id counts as ours | caught | `test_an_answer_without_this_starts_id_is_another_daemon[answer1]` |
| V2 Ctrl-C removes a live child's record | caught | `test_ctrl_c_while_stopping_a_losing_child_names_it` |
| V3 id grammar by prefix `match` | caught | `test_any_other_value_is_ignored_and_said[ab*33]` |
| V4 header middleware inside the guards | caught | `test_refusals_and_streams_carry_it` |
| V10 client drops headers on a 4xx | caught | `test_cli_daemonctl.py::test_start_reports_a_daemon_it_spawned_that_answers_with_its_guard` (stop_scope fakes the probe, so it passes) |
| V11 winner pid never named | caught | `test_an_answer_without_this_starts_id_is_another_daemon[answer0]` |
| V14 constant start id | caught | `test_the_child_gets_a_fresh_start_id_and_the_token_through_its_environment` |
| V17 id length unbounded | caught | `test_any_other_value_is_ignored_and_said[ab*33]` |

Also green on the scratch tree, one file at a time: `test_status_ppid_serial.py` (3), `test_cli_daemon_start_pid.py` (4), `test_cli_start_index_build.py` (8), `test_cli_ux.py -k "daemon or start"` (12).

## Live (port 18595, throwaway TOML, db, data, config and cache dirs; `smoke.py`)
- A single start took 0.91 s. The `/status` header equals `MCUSCOPED_START_ID` in the daemon's `/proc/<pid>/environ`, and `stop` exited 0.
- LAN token race, 7 rounds, and loopback race, 3 rounds: see G1.
- A foreign 401 server on the port: the start is refused before any spawn (`refused the request (HTTP 401)`), and no daemon was spawned.
- Every daemon was killed by PID at the end of each mode. `ps` shows no `mcuscope.daemon`.

## Not covered
- Anything on Windows: the launcher test, whether uv's CI venv gets a launcher, and the id crossing a real launcher.
- A proxy in front of the daemon. Reasoned only: stripping the header reads as lost, and the child's port would clash anyway.
- The whole Python suite and the JS suite (per the brief).

## Scratch (not deleted)
- `~/tt-data/mcuscope-2026-09-26/fixdiff3/`:
  - `host/`, `tools/`: tree copy;
  - `mutate.py`, `mutants.log`, `smoke.py`, `changed.txt`;
  - `citest/`: skip-format probe and its `pytest-report.txt`;
  - `smoke/`: TOML, db, data, config and cache dirs.
