# Fix-diff fixes: firmware partition (base 7db70d8)

Tally: 3 findings fixed (X-webui-fw-2, -5, -6), items 8 and 9 have no firmware or CI part; 2 tools nits from "Checked" fixed.
Scratch: `~/tt-data/mcuscope-2026-09-25/fix-fixdiff-firmware/` (`mutate.py`, `mutate.out`).

## Findings

- X-webui-fw-2: fixed, per the owner's ruling.
  - `ping` and `can stat` return `b.over ? MONITOR_ERR_OVERFLOW : 0` (`monitor_cmds.c`), so an over-long name or state is `ERR 8 overflow` at every seq.
  - INTEGRATION.md: "check it against `MON_OK_PAYLOAD_MAX` and answer `MONITOR_ERR_OVERFLOW` when it does not fit". The same "clamp" wording also sat in `monitor.h` (the `MON_OK_PAYLOAD_MAX` comment) and in SPEC 5's handler typedef comment; both now say the same thing.
  - Tests (`test_payload_wire_clamp`): `ping long name at seq 1`, `ping long name at seq 65535`, `can stat long state at seq 1` (all ERR 8); positive control `ping name that fits at seq 65535` (245-character payload is OK).
  - Revert: `ping ERR8` and `can stat ERR8` mutations are each caught.
- X-webui-fw-5: fixed. The template's probe line now says NACK only when the address does not ACK, and BUSERR, TIMEOUT or BUSY when the bus cannot be probed, as `monitor.h` says. Comment only; `make arm-check` (with `-DMON_TEMPLATE_ALL`) and `make port-template` pass.
- X-webui-fw-6: fixed in the firmware, to match SPEC 2.3.
  - New `event_send(len)` in `monitor.c`: every whole event line goes out through it. It ends an open episode of the line's type (one `g_ovf_count != 0` test when none is open), and the notice goes out first.
  - Callers: the whole branch of `event_end`, `emit_pd`, `monitor_plot`'s `!ps`, `emit_can_event` and the `!e can bus <n> dropped` notice.
  - sim.py already ended an episode on any whole `!` line, so it needed no change. Pinned by `test_the_lines_the_firmware_builds_itself_end_their_episode` (4 cases) in `test_sim_overflow_episode.py`. This test passes both before and after, because the sim did not change.
  - Firmware test `test_overflow_episode_direct` covers five cases:
    - a `!can2` frame leaves a `can` episode open
    - a drained `!can` ends it
    - the unknown-bus notice ends an `e` episode
    - `!pd` ends a `pd` episode
    - `!ps` ends a `ps` episode
  - Revert: each of the 5 call sites switched back to `write_line`, and the type compare replaced by `if (1)`. Each of the 6 mutations is caught (`mutate.out`).
- X-webui-fw-8, X-webui-fw-9: not fixed here. Every item in them is in `host/mcuscope/webui/*` or `host/tests/webui_js/*`, which the parallel agent owns. Neither has a firmware or CI part.
- Nits from "Checked, nothing found" (tools/):
  - `tools/check_dist.py`: removed the unused `import urllib.error`.
  - `tools/webui_smoke.py`: docstring `--port` line now reads "refused if 127.0.0.1 cannot bind it". The old wording claimed "refused if anything holds it", which is not true on Windows.

## Verified

- `make run` and `make asan` in `firmware/tests`: 323/323 each. `make families`: all 6 builds pass. `make port-template`: 10/10.
- `make arm-check` with the STM32CubeIDE arm-none-eabi-gcc 13.3: passes.
- `uv run python -m pytest tests/test_firmware_monitor.py` 7 passed; `tests/test_sim_overflow_episode.py` 14 passed; `tests/test_check_dist.py tests/test_webui_smoke.py` 12 passed.
- `ruff check` is clean on the touched Python files.
- Footprint (M0+, `-Os`, arm-none-eabi-size): `monitor.c` text 4716 -> 4732 B, `monitor_cmds.c` 2238 -> 2246 B. That is +24 B flash and no RAM change.
- `mutate.py`: 8 mutations, run in a scratch copy of `firmware/`, all caught.

## Not verified

- Windows: nothing platform-specific changed. The webui_smoke docstring's Windows claim is taken from the fix-diff report, not driven.
- No `.github/workflows/*` change was needed: the fix-diff report found the CI YAML clean. The apt `gcc-arm-none-eabi -Werror` build and the pwsh `check_dist.py serve` step were not run in CI.
- The three projects that vendor `firmware/monitor` (charger-test, charger_control, relay_control) now differ from upstream and need to be re-copied.

## CHANGELOG lines

- Firmware monitor: `ping` and `can stat` answer `ERR 8 overflow` instead of a truncated `OK` when the port name or CAN state string does not fit the line (breaking for a shim with an over-long `name` or state string).
- Firmware monitor: an over-long event episode now also ends at a whole `!can`, `!pd`, `!ps` or monitor-built `!e` line of its type, as SPEC 2.3 says, so its `cut=<n>` notice is no longer held until the quiet second.

## Needs another batch

- None.

## Needs Windows

- None.

## Needs a browser

- None.

## Cleanup owed

- Private copies left in scratch. Deleting them is a recursive delete, which needs the owner's confirmation:
  - `fw/`: a copy of `firmware/` with build outputs
  - `old/`: the HEAD copies of `monitor.c`, `monitor_cmds.c` and `monitor.h`, used for the size comparison
- Also left there: the `*.o` files in the scratch root.

## The two questions

1. Least confident, rechecked:
   - Whether `event_type(type, len - 1)` reads the type the same way `event_end` does for a line with no space (`!x\n`). Excluding the LF makes it identical, and the existing sanitised-type test still passes.
   - Whether calling `overflow_end` from inside `monitor_plot`, between `!pd` and building `!ps` in `g_out`, could clobber the line. It cannot: the notice is built in its own buffer. The `!pd` case asserts the full `!pd` + `!ps` output.
2. Not thought about before:
   - The "clamp" instruction appeared in three places, not one: INTEGRATION.md, `monitor.h`'s `MON_OK_PAYLOAD_MAX` comment, and SPEC 5's handler comment. Fixing only INTEGRATION.md would have left the header an integrator reads first still recommending truncation.
   - `event_send` routes the hot `!ps` path through one extra branch. The cost is one `g_ovf_count` load per sample, which is the smallest way for the firmware to follow SPEC rather than narrowing SPEC.
