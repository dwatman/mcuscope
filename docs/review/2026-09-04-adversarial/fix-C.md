# Fix batch C: CLI and protocol (C1..C11, D6, D10)

Base `7a1120f`. Files touched: `host/mcuscope/cli.py`, `host/mcuscope/cli_output.py`, `host/mcuscope/config.py`, `host/tests/test_cli_contract.py` (new), `host/tests/test_config_bools.py` (new), `host/tests/test_timeline.py` (one existing refusal test), `docs/SPEC.md` (the `mcu sysrq` table row).

## What changed

### C1 - an unwritable stdout no longer exits 120

`cli_output.py`: module flag `_OUT_FAILED` with `reset_output_state()` / `output_failed()`, and one `_stdout_unwritable(exc)` helper that sets the flag, points stdout at devnull and writes `cannot write output: ...` to stderr.
`out_json` and `emit_stream` each gained a non-EPIPE `except OSError` arm calling it; `emit_stream` then raises `typer.Exit(1)` (the broken-pipe arm still raises `Exit(0)`).
`cli.py`: `reset_output_state()` beside `set_json_mode(False)` in `_dispatch`, and `main()` turns a 0 into 1 when `output_failed()`.

### C2 - Ctrl-C inside a command is 1, not 130

New `_mapped_exit(code)` in `cli.py` maps 130 to `_dispatch_error("interrupted", 1)`.
Applied to **both** exits of the `app()` call: click with `standalone_mode=False` **returns** `Exit.exit_code` as the call's value rather than raising, so patching only the `except EXIT_EXCEPTIONS` arm (as the finding proposed) left the live path at 130. The test caught it.

### C3 - `mcu log export -o FILE` removes a partial file

The write loop takes the `ok`/`finally: os.remove` shape `plot export` and `Client.download` already use, so a `typer.Exit` from `die(..., 3)` mid-walk no longer leaves a short file.

### C4 - `min=0` on the four unbounded counts

`lines --limit`, `tail -n`, `can dump -n`, `session list --limit`.

### C5 + C11 - timeout and min-window bounds

`timeout_ms_option(value, lo=1)` holds the rule; click gets two one-argument wrappers, `live_timeout_option` (`cmd`, `wait`) and `retrospective_timeout_option` (`assert`, lo=0).
**Passing `timeout_ms_option` itself as a click callback is not possible**: a second parameter makes typer call it as `(ctx, param, value)`, so `value` arrived as a `Context` and every `mcu cmd`/`mcu wait` raised `TypeError`. That is the breakage batch A saw at `cli.py:372`; it is fixed and `uv run mcu wait --match x --timeout 50` exits 3 (unreachable) again.
`assert --min-window` gained `min=0, max=MAX_TIMEOUT_MS` plus a local refusal in the daemon's own words - both of its arms (`needs a live window (set timeout_ms too)` and `cannot exceed timeout_ms`), since the daemon refuses `min_window_ms` without `timeout_ms > 0` as well.

### C6 - `_CLOCK_RE` spells `[0-9]`

Arabic-Indic digits no longer open a window.

### C7 - a non-bool per-port flag skips the port

`config.py` checks `autoconnect` and `identify` for `isinstance(..., bool)` before constructing the entry and skips it with a warning, as an out-of-range `baud` already does. Both default to `True`, so falling back resolved a typo towards opening the port.

### C8 - `AI_GUIDE` names every flag

Added `--raw` (wait/assert), `[--baud N]` (attach), `[--wide]` (plot export), `[--save]` (plotjuggler), `[--note "..."]` (session start), `--sim` and `--config` (daemon start). SPEC 4 already documented all six, so no table row needed changing for this item.

### C9 - the two mirrors take their source

`EOL_CHOICES = tuple(p.EOL_BYTES)`; `BUS_OPTION` (and `can dump --bus`, the second copy) take `p.CAN_BUS_MIN/MAX`.

### C10 - a local write failure inside a follow is not "daemon unreachable"

Falls out of C1: `emit_stream` raises `typer.Exit(1)`, which the follow's `except OSError` arm cannot see. Asserted separately.

### D6 - `wait --repeat-ms` reads the send counters with `.get`

### D10 - `mcu sysrq` refuses a non-printable character

`len(char) != 1` kept; a second guard refuses `not char.isprintable()` naming the reason. SPEC 4's `mcu sysrq` row now says "one printable character".

## Revert verification

Each fix was reverted by hand in the working tree, its test run, and the file restored from a byte copy.

| Item | Revert applied | Result |
|---|---|---|
| C1a | `out_json`'s OSError arm deleted | 1 failed |
| C1b | `main()`'s `output_failed()` mapping deleted | 2 failed |
| C2 | `_mapped_exit`'s 130 branch deleted | 1 failed |
| C3 | the export's `finally: os.remove` deleted | 1 failed |
| C4 | `min=0` dropped from all four options | 4 failed |
| C5 | `live_timeout_option` back to `lo=0` | 2 failed |
| C11 | `--min-window` bounds and local refusal deleted | 3 failed |
| C6 | `[0-9]` back to `\d` in the HH:MM group | 1 failed |
| C7 | the bool guard's key list emptied | 3 failed |
| C8 | the `--sim` guide line deleted | 1 failed |
| C10 | `emit_stream`'s OSError arm deleted | 1 failed |
| D6 | `res['sends']` back from `res.get(...)` | 1 failed |
| D10 | the `isprintable` guard deleted | 3 failed |

C9 cannot fail by reverting alone (both mirrors agree today), so it was verified by construction: with a fourth spelling added to `protocol.EOL_BYTES` in process, `set(cli.EOL_CHOICES) == set(p.EOL_BYTES)` is True with the fix and False against the old literal.

## Gates

`uv run python -m ruff check .` clean.
`pytest tests/test_cli_contract.py tests/test_config_bools.py tests/test_cli.py tests/test_review_r2_cli.py tests/test_config_api.py tests/test_wait_repeat.py tests/test_timeline.py tests/test_assert.py -q`: **315 passed, 2 failed**, both in a file this batch does not own (below). `test_review_r2_config.py` passed in an earlier run of the same set.

## Not done, and why

Three existing tests assert the behaviour these fixes change. All are outside this batch's file list, so they are reported rather than edited:

- `tests/test_cli.py::test_windows_einval_from_a_follow_write_is_success[False-None]` - the POSIX leg expects `emit_stream` to raise `OSError` on an untranslated EINVAL. It now raises `typer.Exit(1)`; the leg's `expected` should become 1 with the same `pytest.raises(typer.Exit)` branch as the Windows leg.
- `tests/test_cli.py::test_windows_einval_inside_a_follow_ends_it_as_a_closed_pipe[False-3]` - the POSIX leg expects the follow to answer an untranslated EINVAL with exit 3 ("daemon unreachable"). That is exactly C10; `expected` should become 1.
- `tests/test_regressions.py::test_port_entries_are_typed_and_one_bad_entry_stays_local` - asserts C7's old fallback (`autoconnect = "false"` loads the port at the default `True`). The port is now skipped, so the expected list is `[("good", 9600)]` and the docstring's "defaulted rather than failing the load" needs rewording; the `baud = true` half of the entry is no longer reachable in that fixture and wants its own entry.

Also worth noting for the round, not fixed here:

- `mcu --json tail -n 1` emits its rows through `out_json`, not `emit_stream` (C1's test twin drives it anyway); `emit_stream` is reached only by the follow, which is what C10's test drives.
- C1's crash-log backstop (`~/.local/share/mcuscope/mcu-crash.log` is unkeyed for `mcu`) is untouched: the review files it as a round of its own.
