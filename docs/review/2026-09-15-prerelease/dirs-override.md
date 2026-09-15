# MCUSCOPE_DATA_DIR / MCUSCOPE_CONFIG_DIR / MCUSCOPE_CACHE_DIR (FD2-5)

Owner ruling on FD2-5 (`fixdiff2-daemon.md`): the five platformdirs call sites go through one helper that honours an environment override, and `support.child_env` sets it, so a spawned child is isolated on Windows too (platformdirs resolves the Windows dirs through ctypes, which no environment variable reaches).

Base: `0fc920f`.

## What changed

- `host/mcuscope/dirs.py` (new, 28 lines) - `user_dir(kind)` for `"data"`, `"config"`, `"cache"`: `MCUSCOPE_<KIND>_DIR` if set to a non-empty value, else `platformdirs.user_<kind>_dir("mcuscope")`. Stdlib-only at module level and platformdirs imported inside the function, so `_stdio`'s crash path can call it without a new way to raise. Holds the single `APP_NAME`.
- `host/mcuscope/config.py:140` `default_config_path` -> `user_dir("config")`; `:148` `resolve_db_path` default -> `user_dir("data")`. Module-level `import platformdirs` and `APP_NAME` deleted (the only users were these two lines).
- `host/mcuscope/pidfile.py:58-60` -> `from .dirs import user_dir` (still lazy, and now the platformdirs import is lazy behind it too).
- `host/mcuscope/_stdio.py:305-309` `_crash_dir` -> `from .dirs import user_dir` inside the existing `try`, temp-dir fallback unchanged. Its duplicated `APP_NAME` deleted.
- `host/mcuscope/update_check.py:95-97` `cache_path` -> `user_dir("cache")`.
- `host/tests/support.py:65-67` `child_env` sets the three variables beside the XDG ones, each `<XDG home>/mcuscope`, which is where platformdirs puts the dir on Linux; every existing test that reads a child's data dir as `<data_home>/mcuscope` keeps its path.
- `host/tests/conftest.py:41-42` `isolate_user_dirs` clears the three variables, so one exported in a developer's shell cannot beat the platformdirs patch and put an in-process test back on a real directory.
- `host/tests/test_rulings_cli_closed_pipe.py:38,55` and `host/tests/test_sweep_cli_closed_output.py:57` pass `MCUSCOPE_DATA_DIR` to `child_env`. Without it the override from `child_env` would have won over the in-child `platformdirs` patch and taken those crash logs to the shared child home - the "no crash log" assertions would have gone vacuous, and their positive controls would have failed (they do: reverts R10, R11).

Defaults are unchanged: with no variable set, every call site resolves exactly as before.

Docs: `docs/SPEC.md` 3.3 (one sentence), `docs/ARCHITECTURE.md` module list (`dirs.py` entry), `CHANGELOG.md` [Unreleased] Added, `AI_GUIDE` DAEMON CONTROL section in `host/mcuscope/cli.py`, and the `CLAUDE.md` platformdirs rule now reads "or the `MCUSCOPE_*_DIR` override, via `dirs.user_dir`".

Not changed: `mcuscoped --config`'s help text ("default: platformdirs user config dir"), which is still true when no override is set.

## Tests

`host/tests/test_dirs_override.py`, 10 tests, all driven:

- Each resolver with its variable set, with it set to the empty string (must read as unset), and absent - the platformdirs default being the positive control, asserted as the exact path conftest's isolation produces: `default_config_path`, `resolve_db_path`, `update_check.cache_path`, `pidfile.pid_file_path`.
- `pid_file_path` under the override also creates the directory it names.
- An explicit `storage.db_path` still wins over `MCUSCOPE_DATA_DIR` (the override is the default's directory, not an override of a configured path).
- One variable moves one dir: with `MCUSCOPE_DATA_DIR` set, config and cache stay on platformdirs.
- The value is used as given: no `expanduser`, no app-name suffix.
- `isolate_user_dirs` clears an ambient override.
- A child spawned with `child_env(<home>, MCUSCOPE_DATA_DIR=<override>)` crashing through the real `_stdio.console_entry` writes `mcu-crash.log` under the override, with the sentinel text in it. The override names a directory the XDG home does not, so the file's own path is what says which mechanism placed it; the assertion that it exists there is its own positive control.
- `child_env` names all three variables under its own home.

## Revert verification

Script and backups: `~/tt-data/prerelease-2026-09-15/dirs-override/revert_verify.py`, `backup/`. Each branch: copy the file, hand-undo, run the tests, restore from the copy, `filecmp` the restore. No git. 11 of 11 caught, 11 of 11 restored byte-identical.

| Revert | Branch removed | Caught by |
| --- | --- | --- |
| R1 | the override branch in `user_dir` | 7 of 10 in `test_dirs_override.py` |
| R2 | empty counts as unset (`if override:` -> `is not None`) | the 3 empty-string cases |
| R3 | `config.py` config-dir site | `test_the_config_dir_override_moves_config_toml` |
| R4 | `config.py` db-path site | `test_the_data_dir_override_moves_the_default_capture_db` |
| R5 | `pidfile.py` site | `test_the_pid_record_follows_the_data_dir_override` |
| R6 | `update_check.py` site | `test_the_cache_dir_override_moves_the_update_cache` |
| R7 | `_stdio.py` crash-dir site | `test_a_child_crash_log_lands_in_the_childs_data_dir_override` |
| R8 | the three `child_env` lines | `test_child_env_names_all_three_dirs_under_its_own_home` |
| R9 | the `conftest` delenv loop | `test_the_conftest_isolation_clears_an_ambient_override` |
| R10 | `MCUSCOPE_DATA_DIR` in the sweep child's env | `test_a_crash_notice_into_a_full_stderr_is_logged_and_exits_1` |
| R11 | `MCUSCOPE_DATA_DIR` in the closed-pipe child's env | `test_a_crash_with_a_closed_stderr_is_still_logged_and_exits_1` |

## Gates

- `uv run python -m ruff check .`: clean (two `I001` import-order errors from the edits were fixed with `--fix` before the run).
- `uv run python -m pytest -q -p no:cacheprovider`: 2023 passed, 1 skipped, 376.58 s (`full-suite.log`), then 2024 passed, 1 skipped, 390.17 s on the final tree after the reverts (`full-suite-2.log`, one test more: the conftest-isolation test was written after the first run started). Logs under `~/tt-data/prerelease-2026-09-15/dirs-override/`.
- No em or en dashes in any changed file.

## The two questions

1. **Least confident, and rechecked.** That the override could not silently break the tests that already isolate a child by patching `platformdirs` inside it, since `child_env`'s new variable outranks that patch. I drove it rather than reasoned about it: reverting each of those two files' env line (R10, R11) makes their crash-log positive control fail, which is exactly the failure the unfixed combination would have produced - and, worse, the neighbouring "no crash log written" assertions would have passed vacuously on an empty directory that was never the one being written to. Both files now pin the dir through the variable that wins.
   Unchecked for want of the platform: nothing here has run on Windows, and Windows is the whole reason the override exists. What Linux proves is that the variable is consulted ahead of platformdirs at all five sites; what only Windows can prove is that this is the *only* mechanism that works there. That belongs on the owed Windows leg (`docs/REVIEW_LOG.md` checklist).
2. **What should we have checked that we have not thought about.**
   - Other user-dir resolutions that should have joined the five: swept `host/mcuscope/*.py` for `platformdirs`, `expanduser`, `AppData`, `.local/share` and `.config/`. The only remaining `expanduser` calls are on paths the user typed (`storage.db_path`, `--config`), which must keep expanding `~`. `APP_NAME` now has exactly one definition in the repo (it had three).
   - The `mcu daemon start` parent and the `mcuscoped` child it spawns must agree on the pid path: they do, because the child inherits the environment, so both read the same variable. A test drives the resolver, not that pair; the existing spawn tests cover the pair and are unchanged.
   - The startup log shares `_crash_dir` with the crash log, so it follows the data-dir override too. Not separately tested here: `test_pidfile.py::test_daemon_releases_pid_file_on_sigterm` already asserts both files' location for a spawned daemon under `child_env`.
   - A relative or trailing-separator override is used as given by design (SPEC says absolute is recommended, not enforced), so there is no refusal path to drive.
