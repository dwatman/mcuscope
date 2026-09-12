# CLI fix batch, 2026-09-12 (HEAD c15b7c6)

Files touched: `host/mcuscope/cli.py`, `host/mcuscope/cli_client.py`, `host/mcuscope/update_check.py`, `docs/SPEC.md` (section 4 only), `host/tests/test_cli_export.py`, new `host/tests/test_cli_r2026_09_12.py`.
`cli_output.py` needed no change: `_fmt_value` is already module level, so "export it" is one name added to `cli.py`'s existing `from .cli_output import ...` block.

Every revert verification ran through `/tmp/rev-2026-09-12/fix-cli/mutate.py`: it backs up the file, checks **every** anchor before any write, then per item restores the pre-fix text, runs that item's test, and restores from the backup. All 17 mutations were caught (output below).

## A. Adversarial fixes

| id | file, function | change | reverted -> |
|---|---|---|---|
| C3 | `cli.py` `_clock_bounds` (now takes `s`), new `_require_clock_bounds_daemon`, `CLOCK_BOUND_MIN_VERSION = "0.4.0"` | when `--from`/`--to` is given, one `GET /status`; exit 1 naming the daemon version and "0.4.0 or newer" when older. Reuses `update_check.is_newer` (imported inside the function, so the CLI start-up stays httpx-free); `packaging` is **not** a declared dependency (only transitive: `uv pip list` shows 26.2, `pyproject.toml` does not name it), so no new parser and no new dep. Unparsable version = not refused. The four call sites (`lines`, `log export`, `can dump`, `plot export`) pass `s`. | 5 failed |
| C4 | `cli.py` `session_export` | `out_file.lower().endswith(".db")` | 2 failed |
| C5 | `docs/SPEC.md` section 4 | `plot export` row's `[--session S \| --last-ms MS \| --from T --to T]` becomes four separate brackets; one prose line after the table says every bound given is applied and intersects (9.2), naming `lines`, `log export`, `can dump`, `plot export`; a second line states the pre-0.4.0 refusal | doc |
| C6 | `cli.py` `_stream_export`, `log_export` | no code change needed (both kwargs were already there); the test pins them by wrapping `builtins.open` and asserting `newline=""` on the streamed path and `newline="\n"` on the paged one, so it fails on Linux too | 1 + 1 failed |
| C8 | `cli.py` `log_export` | the refusal names the option actually passed (`--limit`/`--decode`/`--changes`/`--names`); `test_cli_export.py::test_log_export_csv_refuses_the_paged_options` now asserts `f"does not take {extra[0]}"` | 3 failed |
| C9 | `cli.py` `session_export` | no sibling treats `-` as stdout (`log export`, `plot export`, `can dump` and the bare `session export` all open a file of that name), so `-o -` is refused naming the token, with and without `--bundle` | 2 failed |
| C10 | `cli.py` new `_stdout_untranslated`, called from `_stream_export`'s stdout arm | `sys.stdout.reconfigure(newline="")` before the first write, guarded like `_stdio.widen_stdout_encoding`. One site covers all three arms (`log export`, `can dump --csv`, `plot export` to stdout) since they all stream through `_stream_export`. Test drives a `TextIOWrapper(newline="\r\n")` so the Linux default cannot hide it | 3 failed |
| C1 / C7 | none | CLI count untouched. Added `test_a_400_from_the_export_endpoint_is_an_error_with_exit_1`: a 400 `{"error": "no such session: nope"}` from `/lines/export` renders `error: no such session: nope` at exit 1 (no such test existed). No mutation: it pins behaviour the daemon batch's fix depends on, not a change of mine | n/a |
| C2 | none | closed daemon-side | n/a |

## B. Improvements

| id | file, function | change | reverted -> |
|---|---|---|---|
| 1 | `cli_client.py` module level, `update_check.py` above `import httpx` | `sys.modules.setdefault("httpx._main", None)` + why. `update_check.py`'s later imports carry `# noqa: E402` (ruff selects E) | 1 failed |
| 3 | `cli.py` `wait` | `timeout: no line matched '^X' on port sim in 1200 ms`; exit stays 2; `--json` byte-identical (asserted). The proposal's optional `(sent N, failures M)` clause was **not** added: the brief's list is pattern/port/waited_ms, and the `--repeat-ms` path already prints the send counts on stderr | 1 failed |
| 6 | `cli.py` `status` | `trimmed=N` appended to the first line when non-zero, quiet at zero and absent | 1 failed |
| 7 (CLI half) | `cli_client.py` `Client.fail` | 503 dies with exit 3. Placed in `fail`, which `json_or_die`, `download` and `stream_text` all route through, so every endpoint gets it, not just `/wait`. Guide's `wait` line gains the clause | 2 failed |
| 8 | `cli.py` `plot_channels` | `last` through `_fmt_value` when it is a float (`int.is_integer()` only exists from 3.12, and the floor is 3.10); `--json` untouched and asserted at full precision | 1 failed |
| 10 | `cli.py` `attach` | `device` optional, `--serial SN` added, both/neither refused by name, body carries exactly one of `device`/`serial_number`; default alias derives from whichever was given. Guide + SPEC 4 | 2 failed (refusal, body) |
| 11 | `cli.py` `can_dump` | `--session` forwarded as `session=`. Guide (SESSIONS block + can dump line) + SPEC 4 | 1 failed |
| 13 | `cli.py` `devices` | header printed after the empty check, same widths | 1 failed |

## Coordinator's extra item: `can dump --to` with `-f`

`cli.py` `can_dump`: refused with `error: --to cannot be combined with -f; a follow has no end`, exit 1.
The refusals were also moved above `_clock_bounds`, so bad usage costs no request (the C3 version check would otherwise have exited 3 first against a dead daemon).
`--from` with `-f` is not refused and is asserted not to be; it still bounds the backfill only, as before.
Guide's can dump line and the SPEC 4 row updated. Reverted -> 1 failed.

## Import-time numbers

`uv run python -X importtime -c "import mcuscope.cli" 2>&1 | tail -3`, best of 3, is **unchanged** (55.4 / 58.1 / 56.4 ms before, 54.9 / 51.8 / 53.6 ms after): `cli.py` imports httpx lazily, so that command never loaded `httpx._main` in the first place.
The win shows on the path that does import httpx:

- `import mcuscope.cli_client, httpx`: httpx cumulative **76.6 / 77.1 / 73.4 ms -> 30.0 / 31.1 / 30.1 ms** (`httpx._main` 41.6 ms -> 0.003 ms), i.e. about 45 ms off every command that makes a request, and off `mcuscoped` startup through `update_check`.
- `rich` modules loaded after `import httpx`: 55 -> 0.

## Gates

- `uv run python -m ruff check .` over my files: clean.
- `uv run python -m pytest tests/test_cli.py tests/test_cli_export.py tests/test_cli_ux.py tests/test_cli_contract.py tests/test_update_check.py tests/test_cli_r2026_09_12.py -q`: **330 passed** (45 of them new), 113 s, random order.
- `grep -nP '[\x{2013}\x{2014}]'` over my files and `docs/SPEC.md`: no matches.
- Mutation run: 17 of 17 caught, `all mutations caught`.

No daemon stack was needed: every new test drives `cli.main` against an `httpx.MockTransport`, so nothing touched the default config or capture.

## CHANGELOG lines (for the orchestrator to merge; `## [Unreleased]`)

### Changed

- `mcu wait` says what it timed out on: the pattern, the port and how long it waited, instead of the bare word `timeout`. Exit code and `--json` output are unchanged.
- `mcu status` prints `trimmed=N` when the capture has dropped lines to stay under its size cap, and stays quiet when it has not.
- `mcu plot channels` renders `last` through the `--decode` formatter, so a 32-bit float reads `0.140901` rather than seventeen significant figures; `--json` keeps the full value.
- `mcu devices` labels its columns.
- `mcu log export --csv` names the option it is refusing (`--limit`, `--decode`, `--changes` or `--names`) instead of always naming the first two.
- A daemon that stops during a long poll (`mcu wait`, `mcu assert`) is exit 3 (daemon unreachable), not exit 1 with `Internal Server Error`.
- Importing httpx no longer drags in its command-line interface: 55 `rich` modules and about 45 ms off every `mcu` call that makes a request, and off `mcuscoped` startup.

### Added

- `mcu attach --serial SN` attaches by USB serial number (the fourth column of `mcu devices`), so a debugger that comes back under a different device name still attaches; the device argument and `--serial` refuse each other, and one of them is required.
- `mcu can dump --session S`, matching every other read command.

### Fixed

- `--from`/`--to` against a daemon older than 0.4.0 is refused naming its version, instead of silently exporting the whole capture at exit 0 (the daemon applies the bound, and an older one drops the parameter it does not declare).
- `mcu session export --bundle -o run.DB` is refused like `run.db` (on Windows they are one file), and `-o -` is refused rather than writing a file called `-.zip`.
- A streamed export to stdout writes the same bytes as `-o FILE` on Windows: `mcu log export --csv > run.csv` was CRLF where the `-o` form was LF.
- `mcu can dump --to T -f` is refused: the follow could not honour the upper bound and streamed past it for ever.

## Follow-up: `-o -` refused on every export

`cli.py` new `_refuse_stdout_token(out_file)`, called from `session_export` (with and without `--bundle`), `log_export`, `can_dump` and `plot_export`: the four commands that take `-o`.
One message on all of them: `error: -o - is not stdout; omit -o for stdout, or give a file path`.
The per-command guard added earlier for the bundle is gone; this replaces it, so no command can write a file named `-` or `-.zip`.

`test_every_export_refuses_the_stdout_token` walks all five invocations, asserts the exact text and exit 1, and (with the cwd moved to `tmp_path`) that the run left no file behind.
Revert-verified: with the guard's body replaced by `return`, **5 failed**, one per parametrisation; tree restored from the copy afterwards.

SPEC 4: the clause moved off the `session export` row into the export paragraph after the table, where it covers all four commands and says stdout is what omitting `-o` gives.
`AI_GUIDE` never documented `-o -`, so it is unchanged (`test_ai_guide_names_every_flag` green).

Gates re-run after the follow-up: same six files, **333 passed** (48 in the new file); `ruff check .` clean over `host/`; no en or em dashes.

## Not done

- **`host/tests/test_port_health.py::test_to_is_one_until_ts_on_the_query_itself` now fails** (2 requests where it asserts 1). It is not one of my files. The cause is C3 as briefed: the version check is one extra `GET /status` on the `--from`/`--to` path, which is exactly what that test counts. Its other assertions still hold (the handler answers `{}` for `/status`, no `version` key, and an unreadable version is deliberately permissive, so `rc == 0` and the `until_ts`/`id_to` assertions pass). One-line fix for whoever owns that file: count only the `/lines` requests, e.g. `assert [p for p in seen if "limit" in p] == [...]`, or filter `seen` by path. Everything else adjacent is green: `test_port_health.py test_timeline.py test_review_r2_cli.py test_session_bundle.py test_export_lines_can.py test_plot_export_decode.py test_stdio.py` = 132 passed, that 1 failed.
- **Improvement 1's test is a subprocess, not in process.** The brief asked for `"rich" not in sys.modules` after `main(['status'])` in process; that cannot hold in the pytest process, because `tests/support.py` imports httpx at module level before any `mcuscope` module runs, so `rich` is already loaded before the sentinel could be set. The test therefore runs a child that imports `mcuscope.cli` first (the console script's real order), stubs the transport, runs `main(['status'])` and asserts `rich` absent, `httpx._main` absent from the non-None modules, and that the request really went through (the status line is in the child's stdout, which is the half a misplaced sentinel would still pass).
- Improvement 3's `(sent N, failures M)` suffix: see the table, deliberately left out.
- `-o -` is refused on `session export` only. The sibling exports still write a file named `-`; that is at least consistent for them (no `.zip` is appended), and changing their contract was not in the brief.
