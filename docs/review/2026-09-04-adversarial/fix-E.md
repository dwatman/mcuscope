# Fix batch E - firmware leg (`firmware/monitor`, `firmware/tests`)

- Base: `7a1120f`, main tree, uncommitted.
- Source report: `docs/review/2026-09-04-adversarial/firmware.md` (F1-F5, Test quality T1-T5).
- Suite: 237 -> 247 checks, all passing. `tests/test_firmware_monitor.py` 2 passed.

## What changed

### E1 - F1: `cmd_can_stat` NULL guard

`firmware/monitor/monitor_cmds.c`, after the `code != 0` return: if the shim answered 0 having set `*state = NULL`, fall back to `"active"` before the `%s`.

Test: `fake_shims.c` gains `fake_can_stat_set_mode(int)` (mode 1 = answer 0 with `*state = NULL`, reset by `fake_reset`), and `test_can_cmds` drives `>7 can stat` in that mode expecting `<7 OK rx=0 tx=0 err=0 state=active`.

### E2 - F3: `monitor_mark` refuses whitespace-only text

`firmware/monitor/monitor.c`, `monitor_mark`: the `!text || !*text` guard becomes a NULL check plus a skip over spaces and tabs, returning `MONITOR_ERR_BADARG` if nothing else remains. Two checks added to `test_mark`.

### E3 - F4: `emit_hex_resp` clamps to the wire budget

`firmware/monitor/monitor_cmds.c`, first lines of `emit_hex_resp`: `resp_max` is clamped to `MON_OK_PAYLOAD_MAX + 1`.

Tests in `test_hex_resp_clamp`:
- Full-buffer dispatch: `spi xfer imu <246 hex chars>` (mode 1 echo) into a `MONITOR_LINE_MAX + 1` buffer, asserting `rc == 0` and `strlen <= MON_OK_PAYLOAD_MAX`.
- Seq independence: the resulting payload length is fed back through the public wire path as `longresp <len>` at seq 1 and at seq 65535; both must give the same answer (both OK).

Deviation from the report's snippet: it used `i2c rd 48 4` at a full buffer, which caps at 64 bytes (128 hex chars) and therefore passes with the fix reverted. `spi xfer` is the only handler that can reach 246 characters through `monitor_dispatch` (`MON_MAX_DATA` is 128), and it is the path the report's own 246-character measurement used.

### E4 - F5: not tightened

One line beside each `(void)argc` in `cmd_ping`, `cmd_info` and `cmd_can_stat`: extra tokens are tolerated on purpose (SPEC 2.4 leniency). No behaviour change.

### E5 - F2: USART error flags in `INTEGRATION.md`

Two-line paragraph added to the `uart_read` section after the RX-interrupt paragraph, as written in the report.

### E6 - test-quality mutations

The report verified **three** surviving mutations, not two (T1, T2 and T3 are all marked VERIFIED SURVIVING; only T4 and T5 were unbuilt). All three are covered:

- T1: `test_hex_resp_clamp` repeats the dispatch at `char resp[6]` and asserts `strlen == 4`, the size where `(resp_max - 1) / 2` and `resp_max / 2` differ.
- T2: `test_overflow` adds an over-length line with seq `4294967295`, asserting silence, which drives `recover_seq`'s own `<= 65535` bound rather than `process_line`'s.
- T3: `test_i2c_scan_bus_shorted` now pins the exact response string (addresses `08` through `58`, 81 tokens, 242-char payload) instead of only its prefix and length.

## Revert verification

Each change reverted by hand in the tree, rebuilt, run, restored. Baseline is 247/247.

| Reverted / mutated | Result | Failing checks |
|---|---|---|
| F1 NULL guard removed | 246/247 | `can stat null state` |
| F3 whitespace guard removed | 245/247 | `mark whitespace-only badarg`, `mark whitespace-only emits nothing` |
| F4 wire-budget clamp removed | 245/247 | `hex clamp respects wire budget`, `hex payload rc is seq-independent` |
| T1 mutation `(resp_max - 1) / 2` -> `resp_max / 2` | 244/247 | `hex clamp leaves room for NUL`, plus the two F4 checks |
| T2 mutation: `recover_seq` drops `*seq <= 65535` | 246/247 | `overflow out-of-range seq silent` |
| T3 mutation: `MON_OK_PAYLOAD_MAX` -> `MONITOR_LINE_MAX - 18` | 245/247 | `shorted-bus scan exact list`, `spi max length response` |

The T3 mutation now also trips `spi max length response`: with F4 in place `MON_OK_PAYLOAD_MAX` bounds the hex payload too. At the real value (245) the longest wire-reachable `spi xfer` response is 238 characters, so nothing on the wire changes.

## Build

- `cd host && uv run python -m pytest tests/test_firmware_monitor.py -q` -> 2 passed (the wrapper runs `make run` and `make asan`).
- Monitor sources only, `-std=c99 -Wall -Wextra -Wconversion -Wsign-conversion -Wshadow -Wcast-qual -Wpointer-arith -O2`: zero warnings.
- Full suite under `-fsanitize=address,undefined -fno-sanitize-recover=all`: 247/247, no sanitizer reports. One `-Wconversion` warning at `monitor.c:660` (`plot_reject`) appears at `-O1` with sanitizers on; it is present identically on `HEAD:firmware/monitor/monitor.c`, so it is pre-existing and not caused by this batch.

## Requires re-vendoring (charger-test, charger_control, relay_control)

Behaviour changes:

1. `cmd_can_stat`: a shim returning 0 with `*state = NULL` now prints `state=active` instead of `(null)` (or faulting on a strict libc).
2. `monitor_mark`: text that is only spaces and tabs now returns `MONITOR_ERR_BADARG` and emits nothing, where it previously returned 0 and emitted a marker the host files as a plain event. A tab-only string changes from `!m @<tick> .` to no output.
3. `emit_hex_resp`: a caller passing a `resp` buffer larger than `MON_OK_PAYLOAD_MAX + 1` now gets a payload clamped to 245 characters. Unreachable over the wire (the command line overflows first); only a direct `monitor_dispatch` caller with a large buffer sees it.

Comment-only, safe to take or skip:

4. Three `(void)argc` comments in `monitor_cmds.c`.
5. The USART error-flag paragraph in `INTEGRATION.md`.

## Not done

- F5 was deliberately not tightened (E4 chose the comment), so `ping`, `info` and `can stat` still accept extra tokens.
- T4 and T5 from the report's Test quality section (mutations it did not build): the DEL / control-byte tolerance case and the CR-mid-line case. Out of the batch's scope, which named only the verified-surviving mutations.
- The report's "second question" items (diffing the three vendored copies, greppable SPEC 5.1 freestanding checks) are outside this batch.
