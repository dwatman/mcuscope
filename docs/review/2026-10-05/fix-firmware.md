# Fix batch: firmware

## OP-3 (FUZZ-2): CAN id and RTR dlc emitted as handed over

- `firmware/monitor/monitor.c` (emit_can_event): `f->id` unmasked; RTR dlc unclamped; the data-frame clamp stays, with a comment.
- Pinned by `firmware/tests/test_monitor.c`: "can std id wider than 11 bits unmasked", "can ext id wider than 29 bits unmasked", "can rtr dlc past 8 unclamped".
- Revert-verified: monitor.c with the OP-3 lines reverted fails exactly those 3 checks (320/323).
- Filter now matches the unmasked id (same code path, `f->id`); no separate test.

## OP-4 (FUZZ-3): hooks

- Ported `fuzz/fp/src_hooks/monitor.c` onto the current source (the file diff against current was only the hook change; `monitor_cmds.c` and `monitor.h` identical).
- Equivalence: `fw_fuzz` seeds 1-3 x 300k iterations (ASan/UBSan, 2 buses), pre-batch monitor.c against the new one with only the OP-3 lines reverted: TX dumps `cmp` identical.
  With OP-3 applied the dumps differ only on `!can` lines (seeds 1, 2: no non-`!can` line differs).
- `make run`, `make asan`, `make families`, `make families-asan`, `make port-template`, and `pytest tests/test_firmware_monitor.py` (7 passed): all green, test_monitor 323/323.
- Revert-verification of the hook branches: existing overflow-episode and plot rebroadcast tests (all in the 323) cover the hooks being set; not separately mutated (see Doubts).

## FUZZ-4: footprint (matrix.sh, app printf, one bus, M0+ -Os / M4F -Os / M0+ -O2)

- core 4445 / 4489 / 6004 B, RAM 968; plot 7325 / 7377 / 10356 B, RAM 1132; +12 B RAM per bus; x86-64 gcc -O2 `.bss` 1243 B; stack 0.35 KB below `monitor_poll` (hook chain counted by hand, stack.py does not follow function pointers: 96+24+32+8+32+8+96+32+20 = 348).
- Per-family savings table (core build, buildx.sh with EXTRA): M0+ -Os CAN 1136 B / 16 RAM, I2C 480, GPIO 152, ADC 148, SPI 108, all 2324 / 16; -O2 CAN 1736 / 16, I2C 536, GPIO 140, ADC 176, SPI 120, all 3156 / 16.
- Scratch and raw output: `~/tt-data/mcuscope-2026-10-05/fix-firmware/` (`fp/matrix_app.txt`, `fp/matrix_noapp.txt`).

## Existing tests edited

- `firmware/tests/test_monitor.c`: `test_can_id_mask` and the RTR half of `test_can_dlc_clamp` (expectations and comments for OP-3; check labels renamed).

## SPEC edits

- 2.5: firmware emits id and RTR dlc as given; out-of-range frame announced by the sys row.
- 5.1: footprint table, RAM, `.bss`, stack and per-family savings restated.

## Guide wording

None.

## Changelog

- Firmware monitor: a CAN id wider than its flags and an RTR DLC past 8 are emitted as given (the host keeps them as generic events with a sys row) instead of being masked or clamped. Re-vendor needed.
- Firmware monitor: plot registry and over-long-event notice link only when `monitor_plot`/`monitor_mark`/`monitor_eventf` are used: core build 0.5 KB flash and 144 B RAM smaller, plot build 0.12 KB larger than before the change.

## Not done

- `firmware/monitor/monitor.h` and `port_template` untouched.
- The vendored copies (charger-test, charger_control, relay_control) need a re-vendor.

## Doubts

- The brief says the dlc clamp follows the same rule. I applied it to the RTR dlc only: a data frame with dlc 9..15 cannot emit more than data[8], and classic CAN defines it as 8 bytes, so that clamp stays and is documented. An RTR dlc of 10+ now emits two digits, which SPEC 2.5 says a sender should not (the host rejects it, which is the intent). Say if you want the data-frame case treated differently.
- Hook branches were not individually mutated; the hook-vs-original TX equality over 900k fuzz iterations is the evidence.
- A `make` run leaves built test binaries in `firmware/tests/` (git shows only `test_monitor.c` modified, so they are ignored).
