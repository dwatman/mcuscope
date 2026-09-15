# Owner rulings: CLI (2026-09-15)

Scope: `cli.py`, `_stdio.py`, CLI tests, SPEC 4, CHANGELOG. Scratch drivers in `~/tt-data/rulings-cli/` (`closed.py`, `both.py`, `revert.py`).

## Needs a decision or another owner

- **Item 2, contradiction.** The brief expects `mcu nosuchcmd` with a closed stderr to exit 2. SPEC 4 says bad usage is exit 1, and the code returns 1 whether stderr is attached or closed. I kept 1.
- **Item 2, crash not reproduced at HEAD.** B-8 was already fixed in 16b39b6: the `exc.show()` guard is at `cli.py:2901-2904`.
  - Driven with stderr a closed pipe, and with `2>&-`: `nosuchcmd`, `--json nosuchcmd`, bare `mcu`, `lines --bogus`, `lines --limit -1`, `status --url`, `daemon`. Every one exited 1 with no crash log.
  - The crash log the brief quotes most likely came from a 0.4.0 build. None exists in `~/.local/share/mcuscope`.
- **`tests/test_daemon_startup.py::test_daemon_start_warns_that_the_named_config_is_missing[option|env]` now fails.** That is expected, because the warning it pins is replaced by the refusal. I do not own the file.
  - Suggested fix: the `option` and `env` cases should expect `rc == 1`, `no such config file: <cfg>`, and no call to `Popen`. `present` stays as it is.
  - `test_rulings_cli_config.py` already covers both cases.
- **SPEC 3.3 line 452** (outside section 4) still says `mcu daemon start` warns about a missing named config. It needs the refusal wording, presumably from whoever owns 3.3 for the `mcuscoped` half.
- **`MCUSCOPED_CONFIG` counts as named.** The CLI refuses a missing file given through the env var as well, matching SPEC 3.3's existing "the file it names (`--config` or `MCUSCOPED_CONFIG`)". If the daemon agent refuses only `--config`, the two sides differ on the env case.
- The ruff failure at `tests/test_session_bundle.py:141` belongs to another agent. Ruff is clean on every file I touched.

## 1. Subscriber cap exits 1 on `tail -f`

- `cli.py:1120-1124`: a close with code 1013 now runs `die("error: too many subscribers (the daemon's subscriber cap is reached); try again later", 1)`.
  - A close with no code, the 1001 shutdown close and connection errors still exit 3.
  - The 1013 sent by the token rate-limiter comes before `accept`, so the client sees it as HTTP 403 (already exit 1), not as a close.
- Tests (`tests/test_rulings_cli_follow.py`), run against the in-process stack with `store.MAX_SUBSCRIBERS` set to 0:
  - `tail -f` exits 1, names the cap, and does not print `stream closed by daemon`.
  - The same with `--json` gives one error object.
  - `wait` under the same cap exits 1 with `too many subscribers`.
  - A shutdown close (1001) exits 3 with `stream closed by daemon` and no cap text.
- `tests/test_cli.py::test_follow_ws_auth_and_capacity_refusals_are_exit_1` (renamed) now expects 1 for 1013.

## 2. Closed-pipe sweep

Every CLI message write was checked. Only the two sites in `_stdio.py` were unguarded.

| Site | Finding | Handling |
|---|---|---|
| `_stdio.console_entry` repaired-stream warning (`_stdio.py:384`) | Unguarded. With fd 1 closed and stderr a closed pipe, every `mcu` call (and `mcuscoped`, `mcu-sim`) exited 120 before `main()` ran. | New `_note()` (`_stdio.py:351`) suppresses `BrokenPipeError` and points stderr's fd at devnull |
| `_stdio.console_entry` crash notice (`_stdio.py:394`) | Unguarded. A crash with stderr closed exited 120, and the notice's `BrokenPipeError` replaced the original exception. | `_note()` |
| `cli.py` usage arm `exc.show()` | Already guarded (B-8) | Tests added |
| `cli_output.err`/`err_write`/`die`/`confirm_or_exit` | Every stderr write goes through `err_write` (guarded) | None needed |
| `cli_output.out_json`/`emit_stream`, all command `print`s, `--help`, `--version`, `ai-guide`, no-args help | stdout: guarded, or handled by the dispatcher's `BrokenPipeError` arm; driven clean with stdout closed | None needed |
| `cli_client.stream_text`, `cli_daemonctl` print | Re-raise to the dispatcher, or stdout via the same arm | None needed |

Tests are in `tests/test_rulings_cli_closed_pipe.py`. Each runs the real `console_entry` in a child with `platformdirs.user_data_dir` patched to `tmp_path` inside the child, so it holds on Windows too.
- Six paths are each run with stderr closed and then with stdout closed: `nosuchcmd`, `lines --bogus`, `lines --limit -1`, `daemon`, `status --url`, and `status` against an unreachable daemon.
  - Each exit code must equal the attached run's code, and no crash log may appear.
  - With stdout closed, stderr must carry that path's own text.
- `--json` with stderr closed still emits the error object.
- stdout set to None with stderr closed: `nosuchcmd`, `status` and `--help` keep 1, 3 and 0. POSIX-only, because on Windows the repair opens the console.
- Positive control: a real crash with stderr closed exits 1, and `mcu-crash.log` contains the sentinel with no `BrokenPipeError`.

## 3. `daemon start --config` naming a missing file

- `cli.py:2301` `_named_config`: takes `--config`, else `MCUSCOPED_CONFIG`, and applies `abspath(expanduser(...))`.
  - A path that is not a file gets `die("no such config file: <resolved>", 1)`.
  - Otherwise the resolved path is returned and forwarded as `--config`. The daemon does not expand `~`, so it opens exactly the file that was checked.
- `cli.py:2341`: `start` checks before the `already running` probe, the pid record and the spawn. The old warning is removed.
- `cli.py:2445-2452`: `restart` checks before `_stop_daemon`. It no longer carries a running daemon's `config_path` when that is the default path.
  - A daemon started without a config reports the default path, so carrying it would refuse a restart that should come back on defaults.
- Tests are in `tests/test_rulings_cli_config.py`. Refused cases, each with its path-specific message, no spawn, no probe and no pid record:
  - a relative path, `~`, and a directory
  - the env var
  - `--json`
  - `restart` with a missing `-c`, and `restart` whose running config was deleted (neither stops the daemon)
- Cases that are not refused:
  - the flag wins over a missing env file
  - relative and `~` paths are forwarded resolved
  - a missing default config still starts
  - `restart` of a daemon running on the missing default config works
- `tests/test_cli_ux.py`: three restart tests used config names that do not exist. They now create the files.

## 4. DAEMON_MIN_VERSION

- Left at `"0.4.0"`, and `__version__` is already 0.5.0 in the tree. No CLI test ties a faked `/status` version to the build version.
  - `STATUS` in `test_cli_r2026_09_12.py` and `test_prerelease_cli_fixes.py` fakes 0.4.0, which is the minimum and still passes.
  - `OLD` fakes 0.3.0 and is still refused.
- Existing weakness, not changed: `test_an_unparsable_daemon_version_is_not_refused` uses `0.5.0.dev3+g1234`, which would pass even if it parsed, because 0.5.0 is newer than 0.4.0. `0.3.0.dev3+g1234` would make it discriminate.

## Revert-verify

Each mutation was made on a copy of the source and the file restored from that copy afterwards; the restored file was compared against the pre-mutation text.

| # | Mutation | Failing tests |
|---|---|---|
| M1 | 1013 arm never matches | 2 follow cap tests + `test_follow_ws_auth_and_capacity_refusals_are_exit_1` |
| M2 | no-code/1001 close exits 1 | `test_tail_follow_refused_at_shutdown_stays_exit_3` |
| M3 | no existence check | 7 config refusal tests |
| M4 | no `expanduser` | `~` refused and `~` forwarded tests |
| M5 | no `abspath` | relative refused-message and forwarded tests |
| M6 | env var not treated as named | env refused and env forwarded tests |
| M7 | `isfile` changed to `exists` | directory test |
| M8 | restart carries the default path | restart-on-default test |
| M9 | restart check removed (start's check runs after the stop) | both restart-refuses-before-stopping tests |
| M10 | start check moved after the probe | relative refusal test (`probes == []`) |
| M11 | repair warning back to a bare `print` | 3 repair-warning tests (rc 120) |
| M12 | crash notice back to a bare `print` | crash positive control (rc 120) |
| M13 | `_note` without the devnull repoint | repair `--help` + crash control (rc 120) |
| M14 | usage `exc.show()` guard removed | 2 `--json` closed-stderr tests (the object is lost, rc stays 1) |

## Docs

- AI_GUIDE:
  - the `tail -f` line now gives exit 3 when the daemon stops and exit 1 at the cap
  - the `daemon start --config` line now describes the refusal, including `MCUSCOPED_CONFIG`
- SPEC 4:
  - after the `daemon status` sentence, the cap is exit 1 on every command, and a follow still exits 3 on a close with no code or 1001
  - the `daemon start` row describes the refusal, the resolution of `~` and relative paths, and restart's handling of the default config
- CHANGELOG `[Unreleased]` Fixed:
  - `tail -f` 1013 added as a sub-bullet of the wait/assert cap line
  - the unreleased "warns" line replaced by the refusal (3 bullets)
  - the exit-120 fix added

Runs: my three new files, `test_cli_ux.py`, `test_cli_contract.py`, `test_stdio.py`, `test_prerelease_cli_fixes.py`, `test_cli_r2026_09_12.py`, and all of `test_cli.py` (166) pass. Everything in `test_daemon_startup.py` passes except the 2 failures noted above.
