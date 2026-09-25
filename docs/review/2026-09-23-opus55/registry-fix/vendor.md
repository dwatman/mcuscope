# Re-vendor of firmware/monitor into three downstream projects (2026-09-25)

Source: mcuscope working tree `firmware/monitor/{monitor.h,monitor.c,monitor_cmds.c}` (HEAD 7db70d8 plus uncommitted fixes).
Previous upstream: a49e52d.
Backups: `~/tt-data/mcuscope-2026-09-25/vendor-backup/<project>/`.
Build scratch (compile script, log, objects): `~/tt-data/mcuscope-2026-09-25/vendor-work/`.
Nothing committed; nothing in the mcuscope repo edited other than this report.

Tally: 3 of 3 projects re-vendored, 0 local edits, 9 of 9 files compile with 0 warnings, 1 required shim edit plus 1 pre-existing shim bug (both charger-test).

## Method

- Local-edit check: `gcc -fpreprocessed -dD -E -P` of each target file against `git show a49e52d:firmware/monitor/<file>`, plus `cmp` of the raw bytes.
- Compile: `arm-none-eabi-gcc` 13.3 (STM32CubeIDE) with `-mcpu=<board> -mthumb -std=c99 -Os -Wall -Wextra -Wformat=2 -Wconversion -c`.
  The `-I`, `-D`, `-mfpu` and `-mfloat-abi` flags come from each project's own `build/*/compile_commands.json` entry for that file.

## Contract changes since a49e52d that affect shims

- `mon_can_filter` has been removed from monitor.h and the monitor no longer calls it. A shim must not program a CAN hardware filter for the monitor.
- An I2C probe (`wr_len == 0 && rd_len == 0`) must return BUSERR, TIMEOUT or BUSY when the bus cannot be probed, never NACK.
- A variable-length OK payload that does not fit `MON_OK_PAYLOAD_MAX` now answers `MONITOR_ERR_OVERFLOW` (ERR 8) and is not cut.
  `can stat` does this in the monitor: shims need no change unless their `state` string can exceed about 200 bytes.
- `!p` overflow notices are now sent once per episode. This is internal to the monitor and changes nothing for shims.
- `monitor_init()` also resets the once-only `!e` notices, for example `!e can bus <n> dropped`.

## charger-test (`charger-test/charger-test_cmake`, git, branch master)

- **Local edits:** none. All three files were byte-identical to a49e52d.
  - The comment-stripped comparison flagged monitor.h as different. The cause was the path-bearing `MON_WEAK redefined` warning, which my `2>&1` folded into the hash; with stderr dropped the outputs match.
- **Git state:** `src/monitor/` was already uncommitted before this change. The working tree held the a49e52d copy over HEAD 0972296. After the copy, `git diff` there shows the combined change from HEAD to the new version.
- **Copied:** `src/monitor/monitor.h`, `monitor.c`, `monitor_cmds.c`.
- **Compile** (cortex-m33, `-mfpu=fpv5-sp-d16 -mfloat-abi=hard`, `-DSTM32C542xx`):
  - `monitor.c` and `monitor_cmds.c`: 0 warnings.
  - `src/monitor_port.c`: 0 errors, 1 warning. The warning is in the vendor DFP header, not the shim: `stm32c5xx_dfp/Include/stm32c5xx.h:225:13: -Wconversion unsigned int to uint8_t`.
- **Shim edits needed** (`src/monitor_port.c`):
  - Delete `mon_can_filter` (lines 82-93). Nothing calls it, and it no longer has a prototype. It still compiles (only `-Wmissing-prototypes` would flag it) and lands in the object as a dead symbol.
  - Pre-existing bug, present before this re-vendor: `mon_info_extra` returns the `snprintf` count. The monitor appends extras only when the return value is `== 0`, so `txdrop=` and `printfdrop=` never reach `info`. Fix: call `snprintf(...)`, then `return 0;`.
  - Nothing to add: the shim defines no I2C function, and the `can stat` state comes from `canGetStats`.

## charger_control (`STM32_firmware/charger_control`, not under git)

- **Local edits:** none. The files were byte-identical to a49e52d (same monitor.h hash artefact as above).
- **Copied:** `Core/monitor/monitor.h`, `monitor.c`, `monitor_cmds.c`.
- **Compile** (cortex-m4, `-mfpu=fpv4-sp-d16 -mfloat-abi=hard`, `-DMON_CAN_BUSES=2 -DSTM32F412Rx`): `monitor.c`, `monitor_cmds.c` and `Core/Src/monitor_port.c` compile with 0 warnings and 0 errors.
- **Shim edits needed:** none.
  - It never defined `mon_can_filter`.
  - The `mon_can_stat` state is at most 47 bytes, well inside the payload bound.
  - It defines no I2C function.

## relay_control (`STM32_firmware/relay_control`, not under git)

- **Local edits:** none. The files were byte-identical to a49e52d.
- **Copied:** `Core/monitor/monitor.h`, `monitor.c`, `monitor_cmds.c`.
- **Compile** (cortex-m0plus, `-DSTM32C092xx`, MON_CAN_BUSES at its default of 1): `monitor.c`, `monitor_cmds.c` and `Core/Src/monitor_port.c` compile with 0 warnings and 0 errors.
- **Shim edits needed:** none. It has no `mon_can_filter`, no I2C, and a fixed state string.

## Not verified

- No full project build or link was run, and nothing was flashed or run on hardware.
- I did not check whether the host parses charger_control's `can stat` state correctly. It contains spaces (`active tec=0 rec=0 lec=0`) and has behaved this way since before this change.
