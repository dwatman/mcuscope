# Fix batch C2: config.py, lockfile.py, daemon.py, update_check.py

HEAD at start: `0b5eed9` (wave 1). Nothing committed.

## What changed

### F4 - an unreadable config is a startup failure, not a traceback
`config.py` `load_config` gains `except OSError -> ConfigError(f"{cfg_path}: cannot read: {exc}")`, mirroring `_read_doc`'s conversion on the write path. Covers a directory as the config path, a root-owned file, and the TOCTOU between `exists()` and `read_text()`.

### F5 - a corrupt holder record still refuses the second daemon
`lockfile.py` gains module-level `_format_started(since)`, used by `LockError._describe`. It rejects bool, non-numeric, non-finite and out-of-range values (0 .. `_MAX_STARTED` = year 2100) and wraps `time.strftime`/`time.localtime` in `except (OverflowError, ValueError, OSError)`, answering `"an unknown time"`. `LockError` is now constructible from any record that decodes as JSON, so the SPEC 3.2 refusal (with the `--ignore-capture-lock` hint) survives `started = 1e300`.

### SD1 - a wrong-typed bool fails the load (SPEC 3.3 wins)
`config._as_bool` gains `strict: bool = True` and raises `ValueError(f"[{where}] {key} must be true or false, not {value!r}")`, exactly like `_as_int`/`_as_str`; `load_config`'s wrapper names the file. `storage.auto_session`, `update.check` and `plotjuggler.enabled` all refuse. The per-port `autoconnect` passes `strict=False` (warn and default), keeping registry class 16: one bad port entry must not take the whole file down. That is what the existing `test_port_entries_are_typed_and_one_bad_entry_stays_local` pins, and it still passes unchanged.

### F10 - unique temp siblings
`config._write_doc`: `<name>.<pid>.tmp`. `update_check._save_cache`: `update.json.<pid>.tmp`. Both keep the same `replace_atomic` landing and the same unlink-on-failure.

### F11 - startup refusals on stderr
Five `print`s in `daemon.py` gain `file=sys.stderr` (`import sys` added): the config `ConfigError` arm, the `LockError` refusal, the `--ignore-capture-lock` WARNING, the `cannot claim <lockfile>` OSError arm, and the port-conflict arm. `web UI: <url>` and `_warn_if_exposed` stay on stdout (not refusals).

### F14 trio
- (a) `cli_daemonctl.DAEMON_START_TIMEOUT_S = _start_timeout_default` (the function, not its value). click calls a callable default at invocation, so `MCUSCOPE_START_TIMEOUT` is read per `daemon start` instead of being frozen at import. Verified through typer 0.27 / click 8.4: the value arrives as a float and `--help` renders `[default: (dynamic)]`. cli.py needs no edit (it only passes the name through as the option default), which is why this fitted the one-constant budget.
- (b) `daemon._apply_overrides`: `if args.host is not None:` plus a `ConfigError("--host must be a host name or address, not empty")` for an empty/whitespace value, matching `--port 0`.
- (c) `_release_pid_on_terminating_signal` wraps `signal.signal` in `try/except ValueError`, printing a one-line warning to stderr and continuing (daemon.py has no logger; print is the file's convention).

### RG-F17 - the loader's baud ceiling is the API's
`config.MAX_BAUD = 100_000_000` added (mirrors `server.MAX_BAUD`; a test pins the two equal). A port whose `baud` is the right type but outside 1..MAX_BAUD is warned about and the **entry is skipped**, per the ruling, rather than loaded at the default: a loaded entry makes the settings dialog's ports save 422 forever. `_as_int`'s upper bound for baud moved from `_INT_MAX` to `MAX_BAUD` (wrong-type still warns and defaults, class 16).

Note for a later batch (server.py is batch A2's): the ceiling now exists in two places. `test_the_loader_ceiling_is_the_api_ceiling` fails if they drift; the tidier end state is `server.py` importing `config.MAX_BAUD` the way it already imports `MIN_DB_CAP_BYTES`.

## Tests

New: `host/tests/test_review_r2_config.py` (22 tests).

Existing tests updated, all forced by item 5 or item 3 (named as required):
- `tests/test_capture_lock.py::test_a_second_daemon_on_one_capture_is_refused_before_it_serves` - `.out` to `.err`.
- `tests/test_capture_lock.py::test_the_override_downgrades_the_refusal_to_a_warning` - `.out` to `.err`.
- `tests/test_regressions.py::test_a_lock_dir_that_cannot_be_written_is_a_startup_failure` - `.out` to `.err`.
- `tests/test_regressions.py::test_port_override_is_bounded_like_the_config_key` - `.out` to `.err`.
- `tests/test_regressions.py::test_daemon_declines_to_start_on_a_taken_port` - `.out` to `.err`.
- `tests/test_regressions.py::test_config_refuses_to_coerce_a_quoted_boolean` - **pinned the old warn-and-default bool behaviour** (item 3). Rewritten to drive the three wrong-typed bodies through `pytest.raises(ConfigError, match="... must be true or false")`; the real-boolean half is unchanged.

`tests/test_daemon_startup.py` needed no change: it asserts on no daemon stdout.

## Revert verification (mutation, test selection, outcome)

Script: `/tmp/claude-1000/review-r2/c2-revert.py` (restores the file after each run). Every fix reverted, its test run, all failed; with the fixes in place all pass.

| mutation | test selection | with fix | reverted |
|---|---|---|---|
| drop `except OSError` in `load_config` | `-k 'unreadable or directory'` | pass | 2 failed |
| `_format_started` back to bare `isinstance` + `strftime` | `-k started` | pass | 7 failed |
| `_as_bool` warns and defaults | `test_regressions.py -k quoted_boolean` | pass | 1 failed |
| `_write_doc` fixed `.tmp` name | `-k config_writer` | pass | 1 failed |
| `_save_cache` fixed `.tmp` name | `-k update_cache_writer` | pass | 1 failed |
| config refusal back on stdout | `-k config_refusal` | pass | 1 failed |
| port conflict back on stdout | `-k port_conflict_prints` | pass | 1 failed |
| `DAEMON_START_TIMEOUT_S = _start_timeout_default()` | `-k start_timeout` | pass | 1 failed |
| `if args.host:` | `-k empty_host` | pass | 1 failed |
| `signal.signal` unguarded | `-k signal_registration` | pass | 1 failed |
| baud skip removed | `-k api_refuses` | pass | 1 failed |

Two test-hygiene notes found by the mutation pass and fixed:
- the first `update.json.tmp` test passed under its own mutation (it only checked no leftover `.tmp`); it now captures the temp name through a `replace_atomic` spy, like the config one.
- the first `--host ""` test **hung** under its mutation, because the unfixed code starts a real uvicorn on 8765. It now stubs `daemon_mod.uvicorn.run` with a `pytest.fail`, so the mutation fails instead of binding a port.

## Gates

From `host/`, `uv run python -m pytest`:
`tests/test_review_r2_config.py tests/test_config_api.py tests/test_daemon_startup.py tests/test_capture_lock.py tests/test_update_check.py tests/test_regressions.py tests/test_cli.py` - all pass.
`uv run python -m ruff check mcuscope tests` - clean.
No em dashes or en dashes in any touched file.

## Contradictions / notes for the orchestrator

- None of the rulings contradicted each other. SPEC 3.3 already states the bool rule, so no doc edit was needed (docs batch owns any wording).
- `docs/SPEC.md` untouched, nothing committed.
