# Firmware and wire-protocol parsers (aspect `firmware`)

Probes live in `~/tt-data/mcuscope-2026-09-23/firmware/` (`fwprobe.c`, `diff_pd.py`, `diff_cmd.py`, `fuzz_host.py`, `integ_can.c`, `reent.c`, `trunc.c`, `filt.c`, `edge.py`, `sim_emit.py`).
Python probes run from `host/` with `uv run python <probe>`.
No clang or libFuzzer here, and hypothesis is not installed, so every fuzzer is a seeded random harness.

## FIRMWARE-1 HIGH CONFIRMED: the INTEGRATION.md CAN RX ring drops frames silently on a single-bus target

- Where: `firmware/monitor/INTEGRATION.md:250` (ISR local `mon_can_frame_t f;`) and `:266` (`*f = can_rx[can_rx_tail];`).
- Failure: the example ISR fills a stack-local frame through `bxcan_read_fifo0` (id/dlc/data/ext/rtr) and never initialises `bus`, as line 383 tells a single-bus port to do ("never set `f->bus`").
  The pop then copies the whole struct from the ring, which overwrites the monitor's pre-zeroed frame (`drain_can`, `monitor.c:843`).
  ISR stack residue lands in `bus`, and `drain_can` drops any frame whose bus is above `MON_CAN_BUSES` (`monitor.c:851`), with no event, no counter and no warning.
  The shim's own `can stat` rx counter keeps climbing, so the board looks healthy while `!can` events vanish, intermittently and build-dependently.
  The prose at `:270` ("an untouched ... bus as bus 1") is true only for a pop that writes field by field, which the example does not do.
- Repro: `integ_can.c` is the section 4 ring copied verbatim.
  Built with `-ftrivial-auto-var-init=pattern` (stands in for non-zero stack residue), the output is `frames pushed by ISR: 3, !can events emitted: 0, ring bus field: 254`.
  With `=zero` all 3 events are emitted. `-Wall -Wextra` prints nothing.
- Fix: `mon_can_frame_t f = {0};` in both ISR examples (`:250` and `can_rx_push` at `:303`), plus one line saying a pop that copies a whole struct must copy an initialised one.
  The same garbage can also reach `ext`/`rtr` as a `bool` byte other than 0 or 1 (UB) when a driver leaves them unset.
- Class: 41 (callee-filled memory), reached through the docs: the monitor's zeroing is correct, and the documented shim defeats it.

## FIRMWARE-2 HIGH CONFIRMED: an over-long `!p` event is truncated mid-number into a valid line carrying a wrong value

- Where: `monitor.c:747` (`monitor_eventf` clamps to 255 bytes), sanctioned by SPEC 2.3 ("Event lines are truncated to the limit", `docs/SPEC.md:109`) and `INTEGRATION.md:422`.
- Failure: the cut lands anywhere, so the last `name=value` pair of an ad-hoc plot line keeps a prefix of its digits.
  The host parses the result as a well-formed sample: `current_ma=123456` is stored as `12`.
  SPEC 2.3 refuses to truncate OK payloads for exactly this reason ("a cut hex payload cannot be distinguished from a short one"), and events hit the same problem.
  A 20-cell pack on the charger boards is a realistic way to exceed 255 bytes.
- Repro: `trunc.c` emits `!p 4000000000 cell00=1000 ... cell18=1018 current_ma=123456` (257 bytes) through `monitor_eventf`.
  The wire carries `... current_ma=12` (255 bytes), and `protocol.parse_plot_adhoc` returns 20 points ending `('current_ma', 12.0)`.
- Fix (owner should pick): cut an over-long event back to its last space, so a whole token is dropped rather than altered.
  The alternative is to drop the line and count it (or emit `!e`).
  Either way SPEC 2.3 and INTEGRATION.md:422 need to name the rule.
  A whole-token cut still loses the trailing pairs silently; dropping the line is the only option that never mis-stores data.

## FIRMWARE-3 MEDIUM CONFIRMED: a command handler that calls `monitor_poll()` answers one command twice and loses the next

- Where: `monitor.c:964-971` (`assemble_one` resets `g_line_len` only after `process_line` returns) and `monitor.c:954` (argv points into `g_line`).
- Failure: a handler that keeps the superloop alive while it waits (`for (...) monitor_poll();`) re-enters line assembly with `g_line_len` still set.
  The next pipelined line is appended to the tokenized current one, hits the embedded-NUL check, and is answered `ERR 2 badarg` under the **outer** seq.
  Its own seq is never answered, so the daemon times out.
  The outer handler's argv is then rewritten under it, and the outer command gets a second response.
  `monitor.h:7` says "NOT REENTRANT ... call every monitor entry point from the same context"; a handler runs in the same context, so an integrator has nothing that names this pattern as forbidden.
  INTEGRATION.md:142 invites blocking handlers without saying "do not poll from one".
- Repro: `reent.c` registers `wait`, which polls 3 times, and feeds `>1 wait abc\n>2 wait xyz\n>3 ping\n`.
  Output: `<1 ERR 2 badarg`, `<3 OK monitor 1 reent`, `<1 OK argv-before=[wait abc] argv-after=[ping abc>2 wait xyz]`. Seq 2 is never answered.
- Fix: a static `g_in_dispatch` flag.
  A nested `monitor_poll()` skips RX assembly (it can still drain CAN and rebroadcast plots), and `monitor.h` plus INTEGRATION section 3 say so in one line.

## FIRMWARE-4 MEDIUM CONFIRMED: one non-finite `f4` drops every channel of the sample, and SPEC 3.7 describes a per-value drop that cannot happen

- Where: `protocol.py` `_decode_field` / `_decode_plot_sample_tokens` (the whole sample returns None), `plots.js decodePlotField` (the same), `pjstream.py:156` (a per-value `isfinite` filter), `docs/SPEC.md:1032`.
- Failure: a firmware reporting NaN in one `f4` channel (for example "sensor not ready") loses the finite values of every other channel in that line from `plot_points`, the chart and PlotJuggler.
  SPEC 2.5 lists only an unknown definition and a count/width mismatch as reasons a `!ps` becomes a generic event.
  SPEC 3.7 says a non-finite value "is dropped from the datagram rather than emitted", but no decoded sample can carry one: `pjstream.py:156` never sees a non-finite value, because `!p` and `!ps` both refuse the whole line first.
- Repro: `decode_plot_sample('!ps 0 1 7FC00000,0001,0002', parse_plot_def('!pd 0 a:f4 b:u2 c:s2'))` returns `None`; with `3F800000` it returns all three points.
  `diff_pd.py` counted 19 firmware-emitted samples lost this way out of 6612.
- Fix (owner should pick): either drop only the non-finite point and keep the rest in protocol.py and plots.js together, or keep the whole-sample rule.
  Keeping it means writing it into SPEC 2.5, correcting SPEC 3.7, and deleting the dead filter.

## FIRMWARE-5 LOW CONFIRMED: the simulator answers `badarg` where the firmware answers `badcmd`

- Where: `sim.py:363` (`spi`), `sim.py:390` (`adc`), `sim.py:225` (`can0`/`can<N>` above the bus count).
- Failure: an unknown or missing subcommand is `ERR 1 badcmd` on the firmware and `ERR 2 badarg` on the sim.
  Examples: `spi foo`, bare `spi`, `adc get`, bare `adc`, bare `can0`, `can3 foo`.
  The sim's own `i2c`, `gpio` and `can` branches already answer badcmd, so an agent trained on the sim is told "fix your arguments" for a command that does not exist.
- Repro: `diff_cmd.py 60000 3`: 632 `adc *`, 139 bare `adc`, 600 `adc get`, 1192 `spi *`, 171 bare `spi`, and about 2600 `can0`/`can3`/`can9` lines, all firmware `ERR1`, sim `ERR2`.
  Driven singly with `edge.py`.
- Fix: the sim answers badcmd for an unknown `spi`/`adc` subcommand.
  For `can0 <unknown>`, SPEC 2.4:128 ("`can0` ... is ERR 2 badarg") is ambiguous: say whether it covers only the known subcommands (the firmware's reading) or the family token alone (the sim's).

## FIRMWARE-6 LOW CONFIRMED: the simulator accepts `-` as the `can tx` flags token

- Where: `protocol.py parse_can_tx_args` reuses `parse_can_flags`, which accepts the event-side `-`.
- Failure: `>7 can tx 100 01 -` answers `<7 OK` from the sim and `<7 ERR 2 badarg` from the firmware.
  The SPEC 2.4 tx grammar is "any of `x`, `r`".
- Fix: reject `-` in `parse_can_tx_args` (its only caller is the sim).

## FIRMWARE-7 LOW CONFIRMED: the simulator turns an unknown long command into `ERR 8 overflow`

- Where: `sim.py:237` echoes `unknown {name}` into the error detail, and `encode_lines` then replaces the over-long response with ERR 8.
- Failure: a 255-byte command line whose name is unknown is answered `ERR 8 overflow` by the sim, although the line fit.
  The firmware answers `ERR 1 badcmd`.
- Repro: `edge.py "b'>57928 ' + b'F'*248 + b'\n'"` prints fw `<57928 ERR 1 badcmd`, sim `<57928 ERR 8 overflow`.
- Fix: bound the echoed detail (or drop it) when the response would exceed the limit, keeping the real code.

## FIRMWARE-8 LOW CONFIRMED: `can filter <id> <mask> x` means different things on the firmware and the sim

- Where: `monitor_cmds.c:27` (`(void)ext`: matching is id/mask only) against `sim.py _can_rx` (an `x` filter drops standard frames).
- Failure: after `can filter 100 7FF x`, the firmware streams a standard frame 0x100, and the sim does not.
- Repro: `filt.c` shows the firmware emitting both `!can 0 - 100 AA` and `!can 0 x 100 AA`.
  The sim probe (`_can_rx`) returns `None` for the standard frame and the event for the extended one.
- Fix (owner should pick): SPEC 2.4 says only that `x` "is passed to the port layer".
  State whether the software filter honours it, then align the one that disagrees.

## FIRMWARE-9 LOW CONFIRMED: `plots.js` and `protocol.py` split plot lines on different whitespace sets

- Where: `plots.js:82,171,217` (`split(/\s+/)`) against `protocol.py` `.split()`.
- Failure: Python treats U+001C..U+001F as whitespace and JS `\s` does not.
  Those bytes survive the daemon's `decode("ascii", "replace")`, so `!pd 3 c:s1 \x1fdZ:u4` is decoded and stored by the daemon while the browser rejects the definition, and the panel stays empty while `mcu plot` has data.
  The reference firmware cannot emit such bytes (it sanitizes); a foreign firmware can.
  SPEC 2.1 says tokens are separated by single spaces, so neither side follows the grammar.
- Repro: `diff_pd.py 60000 7` produced 90 JS-versus-host disagreements, every one containing `\x1f`.
- Fix: split on `" "` (as `split_tokens` does) in both, or at least make the two sets equal. Class 19/22.

## FIRMWARE-10 LOW CONFIRMED: `monitor_eventf` has no printf format attribute

- Where: `monitor.h:98`.
- Failure: `monitor_eventf("p %s", tick)`, a HardFault on target, compiles clean under `-Wall -Wextra -Wformat=2`.
  The idiom documented in monitor.h, SPEC 2.5 and INTEGRATION (`"p %lu ax=%ld", tick, ax_mg`, uint32_t/int32_t with no casts) is also never checked.
  It is UB wherever `uint32_t` is `unsigned int` (x86-64 host builds; other targets SUSPECTED).
- Repro: `fmt.c` compiles with no warnings.
- Fix: `__attribute__((format(printf, 1, 2)))` under a GCC/Clang guard (as `MON_WEAK` is), and cast in the doc examples.

## FIRMWARE-11 LOW: RAM, flash and stack figures

Superseded by FIRMWARE-14, which measures these on Cortex-M0+ and M4F rather than computing them.
The x86-64 `.bss` figure stands: 1300 bytes (1128 + 172) at `-O2` and `-Os`, against 1268 in SPEC 5.1.

## FIRMWARE-12 LOW CONFIRMED: SPEC 2.4 names an error the monitor never emitted

- Where: `docs/SPEC.md:128` ("an older monitor without bus support answers `ERR 1 unknown`").
- Failure: `git log -S'"unknown"'` over `monitor.c`/`monitor_cmds.c` is empty. Every monitor answers `ERR 1 badcmd` (`unknown <name>` is only the sim's detail text).
- Fix: `ERR 1 badcmd`.

## Divergences: monitor, simulator and SPEC (the complete list found)

| Input | Firmware | Simulator | SPEC | Status |
|---|---|---|---|---|
| `spi`/`adc` unknown or missing sub | ERR 1 | ERR 2 | badcmd = unknown command | FIRMWARE-5 |
| `can0`/`can<N>` unknown or missing sub | ERR 1 | ERR 2 | "can0 ... is ERR 2" (ambiguous) | FIRMWARE-5 |
| `can tx ... -` (flags `-`) | ERR 2 | OK | flags = any of x, r | FIRMWARE-6 |
| unknown 255-byte command | ERR 1 | ERR 8 | ERR 1 | FIRMWARE-7 |
| `can filter id mask x`, standard frame | streamed | filtered | unspecified | FIRMWARE-8 |
| `can filter ... xx` | OK | ERR 2 | unspecified (SPEC 2.4 says so) | documented |
| `can filter` id or mask over 32 bits | ERR 2 | OK | documented in SPEC 2.4 | documented |
| `can tx ... 08 r` (padded DLC) | OK | ERR 2 | documented in SPEC 2.4 | documented |
| byte above 0x7F in a command | ERR 2 | accepted (U+FFFD) | documented in SPEC 2.1 | documented |
| embedded NUL in a command | ERR 2 | treated as a token byte | SPEC 5.4 binds the firmware only | not a defect |
| decimal token over 20 digits (`i2c rd 48 000...064`) | OK | ERR 2 | receiver may bound | allowed |
| `*1e999` scale in a `!pd` body | registers | n/a (host rejects the def) | documented gap in SPEC 5.2 | documented |
| tab or U+001F between `!pd` fields | refused | n/a | single spaces | firmware stricter, fine |
| `mark <text>` command | ERR 1 | OK | sim-only (SPEC 7) | documented |
| error detail text | none | present | optional | allowed |

## Checked and fine

- The firmware suite under `-fsanitize=address,undefined -Wall -Wextra -Wconversion -Wsign-conversion -Wshadow -Wformat=2 -Wcast-align -Wpedantic` passes 247/247.
  It also passes with `-funsigned-char` (the ARM default), `-fshort-enums`, and `MON_CAN_BUSES=1` and `=9` via `fwprobe`.
  The only `-Wconversion` hit is harmless: `monitor.c:660`, the promoted ternary.
- `fwprobe fuzz` under ASan/UBSan: seeds 1-5 at 200k iterations and seeds 11-13 at 300k across bus counts 1/2/9. No fault, no invariant break.
  Every TX line is at most 256 bytes, printable, and ends in one LF; every `<` line is a well-formed seq/OK/ERR 1..9.
  Inputs: random, mutated and oversized lines, CR/NUL/0x80/0xFF bytes, 13+ tokens, a lying `uart_read` (SIZE_MAX, max+N), random shim codes (-1, 10, 1000, INT_MIN), short-filling i2c/spi shims, an unterminated `mon_info_extra`, NULL/long/LF `can_stat` state, random partial CAN frames (any bus/dlc/id), app handlers returning full unterminated or non-ASCII `resp` and emitting events/plots/marks mid-dispatch, and interleaved `monitor_plot`/`monitor_mark`/`monitor_eventf`.
- `diff_pd.py`: 90k generated `!pd` bodies through firmware `monitor_plot`, `protocol.parse_plot_def` and `plots.js parsePlotDef`.
  In the direction that matters, the only case where the firmware accepts and the host refuses is the documented `*1e999` scale gap.
  All 6612 firmware-emitted `!ps` samples decode on host and JS to the values of the little-endian struct fed in (apart from non-finite ones, see FIRMWARE-4).
- `diff_cmd.py`: 110k mutated command lines, firmware against the sim. Seq grammar (`0`, `65536`, `+5`, `05`), token caps, CR anywhere, 0x01/0x1F/0x7F bytes, tabs, 255/256-byte boundaries and overflow seq recovery all agree, apart from the table above.
- `fuzz_host.py`: 200k mutated lines through every protocol.py decoder, `PlotDecoder.feed`, and `serial_link._response_seq`.
  None raised, and CAN event, response and 200k structured CAN-frame format/parse round-trips are symmetric.
- `sim_emit.py`: 7 s of simulator output (plain and `--demo`), including `can tx 7FF`, RTR on bus 2 and `mark`. Every `!can`, `!pd`, `!ps` and `!m` decodes on the host.
- Arithmetic reviewed and bounded: `emit_ok` rejects payloads over 245/249 bytes at seq 65535/1; `i2c scan` truncation stays within `MON_OK_PAYLOAD_MAX`; the worst `!can` line is 46 bytes and the worst `!ps` 159; the `!pd` body cap is 249.
  `emit_hex_resp`'s silent clamp is unreachable over the wire (at most 120 SPI bytes fit a command line), and class 48 already covers it.

## Not covered

- The Cortex-M figures are measured in the Footprint section below, with the STM32CubeIDE arm-none-eabi-gcc 13.3. Not measured: xtensa, IAR/Keil, and anything run on target (stack figures are static `-fstack-usage` plus prologue reads).
- No clang, so no libFuzzer or MSan. Coverage-guided fuzzing was not done; the harnesses are random and grammar-aware.
- The vendoring projects (charger-test, charger_control, relay_control) are not checked out here, so whether their ISRs copy the FIRMWARE-1 pattern is unchecked.
- Windows: nothing in this aspect is platform-specific beyond the sim's socket layer, which was not exercised.
- `can.js` (browser CAN view) receives decoded frames from the daemon rather than raw lines, and was not fuzzed.

# Footprint

Toolchain: STM32CubeIDE arm-none-eabi-gcc 13.3.1.
Builds: `-mthumb`, `-ffunction-sections -fdata-sections`, linked with `--gc-sections` against a stub `main` (port callbacks plus a superloop) and the weak default shims.
Figures are the linked ELF minus an empty `main` built the same way, so they include every library member the monitor pulls in.
Scripts: `fp/build.sh`, `fp/matrix.sh`, `fp/mapsum.py` in the scratch dir; each scratch variant is a `fp/src_*` copy.

Variants:

- `core` calls only `monitor_init`/`monitor_poll`.
- `plot` adds `monitor_plot` and `monitor_mark`.
- `full` adds an application `monitor_eventf`.
- "app printf" means the application already links `snprintf` itself, the usual case on a board with a printf debug path.

## Measured totals (nano.specs unless stated)

| Build | M0+ -Os | M0+ -O2 | M4F -Os | M4F -O2 |
|---|---|---|---|---|
| core, app printf: flash / RAM | 4069 / 1076 | 4996 / 1076 | 4105 / 1076 | 4888 / 1076 |
| plot, app printf | 6065 / 1076 | 7652 / 1076 | 6137 / 1076 | 7612 / 1076 |
| core, no app printf | 6832 / 1492 | 7755 / 1492 | 6728 / 1492 | 7527 / 1492 |
| plot, no app printf | 8832 / 1492 | 10403 / 1492 | 8776 / 1492 | 10251 / 1492 |
| core, full newlib (no nano.specs) | 35400 / 3192 | 36324 / 3192 | 28501 / 3192 | 29301 / 3192 |

- `MON_CAN_BUSES=2` costs +44 to +108 B flash and +12 B RAM (one 12-byte filter slot).
- Per object, M0+ -Os: `monitor.o` .text 3448 (1734 without plot/mark), .rodata 49, .bss 988; `monitor_cmds.o` .text 1678, .rodata 156 (the const command table), .bss 80 (92 at two buses).
  The merged string literals are about 0.3 KB more.
  `.data` is 0: no table sits in RAM that could live in flash.
- Largest symbols, M0+ -Os: `monitor_plot` 1212 (the inlined `!pd` validator), `monitor_poll` 928, `g_out` 257, `g_resp` 256, `g_line` 256, `monitor_dispatch` 236, `cmd_can_tx` 200, `cmd_can_filter` 172, `name_iter_next` 162, `g_cmds` 156, `g_plots` 128.
- The monitor's own RAM is 1068 B (1080 at two buses).
  The 1492 B figure adds about 412 B that newlib-nano's stdio pulls in for `snprintf`: `impure` 80, `findfp` 316, plus malloc/sbrk state.
  That stdio RAM exists only when the application has no printf of its own.

## FIRMWARE-13 MEDIUM CONFIRMED: without nano.specs the monitor's `snprintf` links float printf, costing 22 to 29 KB

- Where: `INTEGRATION.md:26-28` ("If you pass a `%f` to `monitor_eventf` you will drag in the soft-float printf; the monitor's own code never does").
- Failure: the claim holds only for newlib-nano.
  Against standard newlib, the monitor's own `snprintf` pulls in:
  - `svfprintf` (5.3 KB) and `vfiprintf` (2.7 KB)
  - `dtoa` (3.3 KB) and `mprec` (1.9 KB)
  - soft-double `adddf3`/`subdf3`/`muldf3`/`divdf3` (7.1 KB)
  - `_udivmoddi4`, and malloc with 1 KB of `.data`
- Measured cost: M0+ -Os core goes from 6832 to 35400 B flash and from 1492 to 3192 B RAM; M4F -Os from 6728 to 28501 B.
  A plain Makefile or CMake project that never chose nano.specs pays this for a monitor that prints no float.
- Fix: INTEGRATION section 1 says to link with `--specs=nano.specs` (CubeIDE's default, not every build's), and the sentence is corrected. FIRMWARE-15 removes the dependency outright.

## FIRMWARE-14 LOW CONFIRMED: the stated figures against measured ones (replaces FIRMWARE-11)

| Claim | Where | Measured |
|---|---|---|
| "roughly 4 KB flash" | SPEC 5.1, INTEGRATION.md:24 | 4.1 KB only for `core` -Os with the application's printf already linked. With plot/mark: 6.1 KB (-Os), 7.6 KB (-O2). Without an app printf: +2.1 to 2.8 KB. |
| "under 1 KB RAM" | INTEGRATION.md:24 | 1068 B (1080 at two buses), +412 B of stdio state without an app printf. Wrong. |
| "roughly 1.0 to 1.1 KB on Cortex-M" | SPEC 5.1 | 1068 to 1080 B. Correct. |
| "1268 bytes of .bss on x86-64" | SPEC 5.1 | 1300. Stale. |
| stack | not stated anywhere | worst chain 688 B on M0+ -Os, 648 B on M4F -Os, before the shim and exception frames |

- Stack chains (static `-fstack-usage` for the monitor, plus prologue reads of newlib-nano's `sniprintf` 136, `_svfiprintf_r` 152, `_printf_i` 64, `_printf_common` 32, `__ssputs_r` 40):
  - `monitor_poll` 104 → `monitor_dispatch` 56 → `cmd_info` 96 → `snprintf` chain 432 = **688** (M0+). `cmd_can_stat` gives 648.
  - `monitor_poll` → `monitor_dispatch` → `cmd_spi_xfer` 280 → shim = 440 plus the shim; `cmd_i2c_wrrd` is 232.
  - `monitor_plot` 120 → `plot_reject` → `monitor_eventf` → `vsnprintf` chain = about 576.
- A CubeMX project's default `_Min_Stack_Size` is 0x400, so the monitor alone uses two thirds of it.
- Fix: state -Os/-O2 flash for core and core+plot, 1.07 KB RAM plus stdio if absent, and a stack line ("about 0.7 KB below `monitor_poll`, plus your shims") in SPEC 5.1 and INTEGRATION section 1.

## FIRMWARE-15 LOW CONFIRMED (owner should pick): the monitor's own `snprintf` costs 2.1 to 2.4 KB and 412 B RAM on a printf-free board

- Where: `monitor.c` `emit_err`, `emit_ok`, `emit_pd`; `monitor_mark` and `plot_reject` via `monitor_eventf`; `monitor_cmds.c` ping, info, can stat, i2c scan, gpio get, adc read.
- Variant `fp/src_nopf`: a 40-line bounded appender (`mon_buf_t`, put char/str/strn/u32/s32) replaces every internal `snprintf`.
  `monitor_mark` and `plot_reject` build their line directly. `monitor_eventf` keeps `vsnprintf`, since it is the public printf-style API, so libc stdio is linked only if the application calls it.
- Behaviour: wire output is byte-identical.
  The firmware suite passes 247/247 under ASan/UBSan, and `FWDUMP=1 fwprobe fuzz` seeds 1-3 (150k iterations each, about 1.5M lines including every handler, the mark, plot-reject and overflow paths) `cmp` identical against the original.
- Measured, flash / RAM before → after:

| Case | M0+ -Os | M4F -Os |
|---|---|---|
| core, no app printf | 6832 / 1492 → 4710 / 1080 | 6728 / 1492 → 4392 / 1080 |
| plot, no app printf | 8832 / 1492 → 6692 / 1080 | 8776 / 1492 → 6384 / 1080 |
| core, app printf | 4069 → 4433 (+364) | 4105 → 4401 (+296) |
| full (app calls `monitor_eventf`) | 8864 → 9256 (+392) | 8820 → 9120 (+300) |

- It also cuts the worst stack chain from 688 to 440 B plus the SPI shim (FIRMWARE-14), and it removes FIRMWARE-13 whenever the application never calls `monitor_eventf`.
- The trade-off: it saves 2.1 to 2.4 KB and 412 B RAM for a board with no printf, and costs about 0.3 to 0.4 KB for a board that already has one, which is what SPEC 2.2 assumes the owner's boards have.
  A leaner appender than this scratch one would narrow that cost.

## FIRMWARE-16 LOW CONFIRMED: two divisions pull the 276 B libgcc divide helper into printf-free M0+ builds

- Where: `emit_dec_u32` (`v % 10`, `v /= 10`) and `mon_parse_dec_u32` (`(UINT32_MAX - d) / 10`); Cortex-M0/M0+ has no divide instruction.
- Variant `fp/src_nodiv` (on top of `src_nopf`): a powers-of-ten subtraction loop, and the overflow test `v > 429496729u || (v == 429496729u && d > 5u)`.
- Measured, M0+ -Os: core 4710 → 4462 (−248), plot 6692 → 6444.
  With an app printf it saves nothing (+3 B), since nano printf needs the helper anyway. No saving on M4F, which has a hardware `udiv`.
- Behaviour: identical. 247/247; fuzz dumps `cmp` identical; `mon_parse_dec_u32` gives the same result on 0, 9, 10, 429496729, 4294967290, 4294967295, 4294967296, 4294967299, `0004294967295`, 42949672950 and the empty string.
- Only worth taking together with FIRMWARE-15.

## FIRMWARE-17 LOW CONFIRMED: every command family is linked whether or not the board has the bus

- The dispatch table references every handler, so `--gc-sections` keeps them all, even where the shim is the weak `nosup` default.
- Measured by deleting a family's rows from `g_cmds` in a scratch copy (M0+ -Os, app printf, core 4069 B):

| Family | Saving |
|---|---|
| CAN | 624 B flash, 12 B RAM |
| I2C | 476 B |
| GPIO | 168 B |
| ADC | 136 B |
| SPI | 108 B |

- A UART-only board pays about 1.5 KB for handlers that can only parse their arguments and answer `ERR 7`.
- Behaviour: deleting the rows changes the wire, since `can tx` becomes `ERR 1 badcmd`.
  A build flag (`-DMON_NO_CAN` and so on) that swaps the family's rows for one `{family, NULL, nosup}` row would keep `ERR 7 nosup`.
  The saving would then be about the figures above, less a few bytes; that form was not built.

## FIRMWARE-18 LOW CONFIRMED: `cmd_spi_xfer` and `cmd_i2c_*` put 128-byte copies on the stack

- Where: `monitor_cmds.c` `cmd_spi_xfer` (`tx[128]` + `rx[128]` = 280 B frame), `cmd_i2c_wrrd` (232), `cmd_i2c_wr` (152), `cmd_i2c_rd` (104).
- Variant `fp/src_inplace` (on top of `src_nopf`):
  - Write data is decoded in place in its argv token, since byte i lands at or before hex digit 2i.
  - Read data goes straight into `resp`, zeroed first as today, and is hex-expanded in place from the end.
- Measured: frames drop to 40 / 56 / 32 / 40 B for +68 B flash (M0+ -Os core 4710 → 4778).
  With FIRMWARE-15 the worst chain becomes about 350 B.
- Behaviour: identical. 247/247; fuzz dumps `cmp` identical over seeds 1-3; `diff_cmd.py 20000 5` against the sim gives the same divergence set and no SPI data mismatch.
  Shims still get separate `tx` and `rx` buffers.
- Without FIRMWARE-15 it lowers nothing that matters, because the `snprintf` chain (688) stays the worst.

## Checked: no waste found

- Three 256-byte buffers are the minimum for the current contract:
  - `g_line` holds argv during dispatch.
  - `g_resp` has to be separate from `g_out`, because a handler may emit events mid-dispatch; the fuzz harness's app handler does, and it holds.
  - Sizing `g_resp` to the sendable 246 would save 10 B but let a handler's own `snprintf` truncate silently instead of drawing `ERR 8`.
- `g_plots`: 4 × 32 B with 3 B padding each; reordering the fields still rounds to 32. `g_filt`: 12 B per bus; a `uint8_t` mode still pads to 12. `g_reg`: 64 B. None pays anything back.
- `g_stage` (64 B): reading straight into `g_line` could drop it, at the cost of the per-poll read clamp and the one-command-per-poll boundary; not built.
- Library pulls in the nano build: `snprintf`/`vsnprintf` with the nano integer printf (about 2.5 KB plus 412 B stdio RAM), `strcmp`/`strncmp`/`strlen`/`memset`/`memcmp`/`memmove` (16 to 36 B each), and on M0+ `__aeabi_uidiv`.
  No `strtol`/`strtod`, no float formatting, no soft-float routines, and no 64-bit divide (`__aeabi_uldivmod`), except through standard newlib (FIRMWARE-13).
- The `!pd` validator costs about 0.9 KB (M0+ 6065 → 5197 with the name, tail and uniqueness checks removed).
  It buys the SPEC 5.2 guarantee that a stream the host would refuse never registers, so it pays for itself, and only boards that call `monitor_plot` link it.
- `-O2` against `-Os` costs +0.8 to +1.6 KB (M0+ plot 6065 → 7652), and `monitor_plot`'s hot path does not need it; building the two monitor files at `-Os` is a free saving an integrator can choose.
