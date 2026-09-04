# Fix batch F, 2026-09-04 adversarial round

HEAD `7a1120f`, applied to the working tree. All thirteen findings of `fix-diff-round.md` are closed.

## What changed

### R1 - `mcu log export -o FILE` deleted a file it could not open (HIGH)

`host/mcuscope/cli.py` `log export`: the `open()` moved out of the guarded region, taking `plot export`'s shape.
`ok = False` and the `finally: os.remove(out_file)` are now reached only once the open has succeeded, so a path that fails to open exits 1 with the file untouched.

Test: `host/tests/test_cli_contract.py::test_log_export_keeps_a_file_it_could_not_open` (a `chmod 444` pre-existing file, skipped on Windows where the mode does not deny write).

### R2 - config loader versus SPEC 3.3 (MEDIUM)

`docs/SPEC.md` 3.3, the two sentences at 502-503 replaced by one that names every carve-out:

> The same mistake inside a `[[ports]]` entry warns and keeps that key's default, except where the default is the dangerous answer: a non-string `alias`, an out-of-range `baud`, and a non-bool `autoconnect` or `identify` skip that entry instead.

No code change: the loader (including the pre-existing `baud` skip) now conforms.

### R3 - `--eol` without `--send` (MEDIUM)

`host/mcuscope/cli.py` `wait()` and `assert_()` refuse it client-side in the daemon's wording, `error: eol applies to send; set send too`, exit 1, beside the other client-side refusals.

Test: `test_cli_contract.py::test_eol_without_send_is_refused_client_side`, parametrised over both commands.

### R4 - `connecting` missing from both gloss surfaces (MEDIUM)

- `cli.py` AI_GUIDE, first in the `disconnect_reason` list: `connecting    no open attempt has resolved yet; wait one retry interval`.
- `webui/statusbar.js` `DISCONNECT_WHY`: `connecting: "opening the port for the first time"`.

Test: the existing chip-hover case in `host/tests/webui_js/statusbar_logic.test.mjs` gained the `connecting` key alongside its `open_failed` and unknown-reason assertions. The guide line has no test of its own (the guide test checks flags, not the reason table).

### R5 - `mcu --version` absent from AI_GUIDE (MEDIUM)

- `test_cli_contract.py::_option_strings`'s `walk()` collects a command's own params before recursing, so group params (the root's global options) are in range; the path for the root reads `<root>`.
- `cli.py` AI_GUIDE GLOBAL OPTIONS gained `--version         client version and interpreter (honours --json)`, in the block's own column width rather than the report's wider one.

Test: `test_ai_guide_names_every_flag`, which now sees the root group.

### R6 - two stale comments (MEDIUM)

`cli.py` `sysrq` and the parametrised test in `test_cli_contract.py` both carried the removed `send_raw` strip. Replaced with the wording the report gives (the constraint, not the mechanism), and the test's assertion message with "a non-printable SysRq character is bad usage, not a write the daemon has to refuse".

### R7 - `monitor_mark`'s whitespace-only refusal (MEDIUM)

Identical clause in `firmware/monitor/monitor.h` and its mirror in `docs/SPEC.md` 5: "text that emits nothing: NULL, empty, or only spaces and tabs, and (on a port with no tick_ms) text whose first word is itself an `@<digits>` tick sigil". Text only; three downstream projects vendor the header.

### R8 - `/plot/export` window freeze (MEDIUM-LOW)

`host/mcuscope/server.py`: `if id_to is None: id_to = store.max_id()`, unconditionally. Every request now fixes one upper bound, so the count guards exactly what the CSV streams. The `_do_assert` twin is unchanged (correct as written).

### R9 - the burst-cap test's floor (MEDIUM-LOW, Windows)

`host/tests/test_wait_repeat.py`: `min(gaps) >= 0.010` replaced by the two assertions from the report - at most one sub-10 ms gap (one re-anchor, never a run) and `max(gaps) < 0.2` (the cadence resumed).

### R10 - case-sensitive socket refusal (LOW)

`host/mcuscope/link.py`: `device.lower().startswith("socket://")`.

Test: `host/tests/test_link.py::test_send_break_over_an_uppercase_socket_url_is_refused_too`.

### R11 - `mon_can_stat`'s `*state` contract (LOW)

`firmware/monitor/monitor.h`: `const char **state);  // init; state = current, may be left untouched`.

### R12 - stale hover on a changed `last_write_error` (LOW)

`webui/statusbar.js` `portsSig` gained `p.last_write_error || ""`.

Test: the write-failure case in `statusbar_logic.test.mjs` now changes the message at an unchanged count and asserts the badge title repainted.

### R13 - close 1008's two meanings (MEDIUM)

- `server.py`: `await websocket.close(code=1008, reason=f"no such port: {port}")`. The two auth closes still send no reason.
- `cli.py` `_follow_ws`: `die(f"stream refused by daemon: {exc.rcvd.reason or 'not authorised'}", 1)`.
- `docs/SPEC.md` 3.4's `/ws` paragraph states that the port refusal carries the reason `no such port: <alias>` and the auth refusals send none.

Test: `host/tests/test_cli.py::test_tail_follow_on_an_unattached_alias_names_the_port` drives `mcu -p typo tail -f` against the stack and asserts "no such port: typo" is reported and "not authorised" is not.

## Revert verification

Each fix was reverted in isolation (file copied, reverted, test run, file restored) to confirm its test fails without it.

| Item | Test | Reverted |
|---|---|---|
| R1 | `test_cli_contract.py::test_log_export_keeps_a_file_it_could_not_open` | fails |
| R2 | none (SPEC text) | n/a |
| R3 | `test_cli_contract.py::test_eol_without_send_is_refused_client_side` | fails |
| R4 | `test_webui_js.py` (statusbar chip hover) | fails |
| R5 | `test_cli_contract.py::test_ai_guide_names_every_flag` (AI_GUIDE `--version` removed) | fails |
| R6 | none (comment and assertion message) | n/a |
| R7 | none (header and SPEC text) | n/a |
| R8 | `test_server_scope.py::test_plot_export_streams_the_window_its_count_guarded` | fails |
| R9 | none (see below) | n/a |
| R10 | `test_link.py::test_send_break_over_an_uppercase_socket_url_is_refused_too` | fails |
| R11 | none (header text) | n/a |
| R12 | `test_webui_js.py` (write-failure badge title) | fails |
| R13 | `test_cli.py::test_tail_follow_on_an_unattached_alias_names_the_port` | fails |

R5's revert doubles as the proof of the walker hoist: `--version` is only visible to the test because a group's own params are now collected.

## Not done, and why

- **R9 has no revert check.** It loosens an assertion; the old one passes on an unloaded machine, which is exactly the point - the failure it removes needs a 20 ms event-loop stall to appear. The test still passes with the fix in.
- **R4's AI_GUIDE line is untested.** The guide test enumerates flags, not the `disconnect_reason` table, and a test that pins the table's text would be pinning prose.
- **R2 chose the SPEC rewrite, not the `identify` fallback.** The report offered either; the ruling was the rewrite, so `config.py` is unchanged.
- `docs/REVIEW.md` and `docs/REVIEW_LOG.md` were not touched, per the batch brief. R1's registry class 49 clause (arm the removal only after the open succeeds) is still owed there.

## Gates

- `uv run python -m ruff check .`: clean.
- `uv run python -m pytest tests/test_cli_contract.py tests/test_cli.py tests/test_link.py tests/test_break.py tests/test_wait_repeat.py tests/test_server_scope.py tests/test_regressions.py tests/test_webui_js.py tests/test_firmware_monitor.py tests/test_config_bools.py tests/test_config_ports_eol.py -q`: **338 passed** (134 s). The one warning is Starlette's pre-existing httpx deprecation.
- `tests/test_config.py` does not exist; the config suites in this tree are `test_config_bools.py` and `test_config_ports_eol.py`, both run above.
- Nothing committed.
