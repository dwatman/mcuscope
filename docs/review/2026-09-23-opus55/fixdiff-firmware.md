# Fix-diff leg: firmware slice (2026-09-24)

HEAD: `b994076c88c875ad43c30789cda6039d147b7abe`

Scope: `git diff 6e4f6f7..HEAD` over `firmware/monitor/*`, `firmware/tests/*`, `host/tests/test_firmware_monitor.py`, SPEC section 5 and the firmware-facing 2.x hunks, checked against `sim.py` and `protocol.py`.
Scratch (copies, harnesses, mutation script): `~/tt-data/mcuscope-2026-09-24/fixdiff/` (`fw-copy/`, `fw-old/`, `diff/evh.c`, `diff/fuzz.py`, `diff/fmt.c`, `diff/bnd.c`, `mut.py`, `fp/measure.sh`).

## Findings

### F1 MEDIUM: the simulator's plain CAN filter still passes extended frames

- Where: `host/mcuscope/sim.py:301` (`if st.filter_ext and not frame.ext`); stale comment at `:65`.
- Defect: FIRMWARE-8 was applied to the firmware (`monitor_cmds.c:45`, `ext == g_filt.ext`) and SPEC 2.4, but the sim only got the `x` half: a plain filter still passes extended frames.
- Scenario: `can filter 102 7FF`, then `can tx 101 01 x` makes the sim echo extended id 0x102 as `!can 1 x 102 01`, which the firmware drops. An agent developing against the sim sees traffic the board will never stream.
- Confirmed: driven (`py/filt.py`): the plain filter plus an extended 0x101 frame returns `!can 1 x 101 01`; the firmware's `test_can_filter` "plain standard only" expects it dropped.
- No test catches it: `test_sim.py:254` uses standard frames only, and `test_sim_error_codes.py` has no filter case.
- Fix: `if st.filter_ext != frame.ext: return None`; reword the `filter_ext` comment; add the firmware's three filter cases (x/plain/all over both kinds) to the sim suite.

### F2 LOW: new formatter branches that no test covers (revert-verified)

- Where: `monitor.c` `mon_put_s32` (the `'-'` and the INT32_MIN negate); `monitor_cmds.c:446` adc `mv != INT32_MIN` omission; `monitor_cmds.c:424` gpio `'1'`; `monitor.c:348` the 16/17-char type bound.
- Defect: each mutation passes `run`, `asan` and `families` (`mut.py`):
  - `mon_put_s32` with the minus dropped;
  - the negate rewritten as UB `-v`;
  - `if (mv != INT32_MIN)` forced true;
  - gpio get always `'0'`;
  - type bound cut to 15 chars.
- Scenario: a later edit that drops a sign or prints `mv=-2147483648` for "not available" ships green. The fake ADC always returns 2048/3300 and `led` is only read at 0.
- Confirmed: the code is correct today (`diff/fmt.c`: every size 1 to 14 against `snprintf`, and a 65521-step sweep of u32 and s32 including INT32_MIN, under ASan/UBSan, 0 failures). The gap is in the suite only.
- Fix:
  - add a fake ADC channel with a negative `raw` and `mv = INT32_MIN`, and one with `raw = INT32_MIN`;
  - add `gpio set led 1` then `gpio get led` expecting `OK 1`;
  - add a 16-char and a 17-char first token to `test_event_overflow_cut`.

### F3 LOW: a one-token over-long marker or `!p` leaves a bare header line

- Where: `monitor.c:353` (`event_end`: `if (cut > 1)` sends whatever precedes the last space).
- Defect: when the only space is right after the type (or after `@tick`), the kept line is `!m @0` or `!p`, which the host stores as a generic event; `monitor_mark` otherwise refuses to spend a line on an empty marker.
- Scenario: `monitor_mark` with a 300-byte text of one token emits `!m @0` then `!e event m overflow`; `protocol.parse_marker("!m @7")` returns None.
- Confirmed: driven (`diff/evh mark`).
- Fix (owner should pick): send only the notice when the kept line holds no token past the type (and the tick for `m`), mirrored in `sim._cut_event`; or accept it and say so in SPEC 2.3.

### F4 LOW: `mcu ai-guide` does not name the new notice

- Where: `host/mcuscope/cli.py:2911` (the `--match "^!e"` line lists only `!e plot 3 badarg def`).
- Defect: an agent reading the guide cannot learn that `!e event p overflow` means a `!p` lost its trailing pairs.
- Confirmed: reasoned (grep: the notice appears only in SPEC and the sim).
- Fix: one line beside the plot example: `"!e event p overflow": an over-long event was cut at a space`.

### F5 LOW: the silent drop behind FIRMWARE-1 is now documented rather than counted

- Where: `monitor.c:971` (`continue` for a bus above `MON_CAN_BUSES`); `INTEGRATION.md:304` now says "drop the frame without a trace".
- Defect: the project rule is that every shed is counted or announced; this one is neither, and the round only documented it.
- Scenario: a vendored ISR that copies an uninitialised frame loses `!can` events while `can stat rx` keeps climbing.
- Confirmed: reasoned; the ruling was docs-only (triage), and the three vendored shims set every field, so the owner's boards are unaffected.
- Fix: a latched `!e can bus <n>` notice, or a drop counter reported in `can stat`; either costs a few bytes.

### F6 LOW: the standard-newlib `monitor_eventf` cost is stated against the wrong baseline

- Where: `docs/SPEC.md:1233`, `INTEGRATION.md:45` ("22 to 29 KB of flash and 1.7 KB of RAM").
- Defect: those figures are the extra cost over the nano build of the same program. Measured over a build without `monitor_eventf`, the cost is 31.4 KB flash and 2.1 KB RAM on M0+, and 24.5 KB flash on M4F.
- Confirmed: driven (`fp/measure.sh`): M0+ plot 6680/1080 against full 38076/3192; M4F 6712 against 31205.
- Fix: say "22 to 29 KB and 1.7 KB more than with newlib-nano", or quote the totals.

### F7 LOW (latent): the read refusal and the hex clamp disagree on what fits

- Where: `monitor_cmds.c:99` (`read_into_resp` refuses `n > resp_max`), against `hex_resp_in_place`, which then silently drops bytes past `(resp_max - 1) / 2`.
- Defect: a read of `resp_max/2 < n <= resp_max` bytes is answered OK with fewer bytes than requested; `n > resp_max` gets ERR 8. Two rules for one condition, and one of them is silent.
- Scenario: unreachable over the wire at the shipped limits (i2c n <= 64, spi <= 120 bytes against a 256-byte `g_resp`); reachable only through the internal `monitor_dispatch` with a small `resp`, which `test_hex_resp_clamp` pins as a silent clamp.
- Confirmed: reasoned from the code and the existing test.
- Fix: refuse in `read_into_resp` when `2n + 1 > min(resp_max, MON_OK_PAYLOAD_MAX + 1)`, then drop the clamp from `hex_resp_in_place` (class 48 form: one budget, one check).

### F8 LOW (latent): `mon_buf_init` writes one byte into a zero-size buffer

- Where: `monitor.c:196-199` (`end = buf + size - 1; *b->p = '\0'`).
- Defect: with `size == 0`, `end` points before the buffer and `buf[0]` is written; the old `snprintf(resp, 0, ...)` wrote nothing, and `hex_resp_in_place` still guards `resp_max > 0`, so the code treats 0 as legal in one place and fatal in another.
- Scenario: only through the internal `monitor_dispatch(..., resp, 0)` (ping, info, can stat, gpio get, adc read); the monitor itself always passes 256.
- Confirmed: reasoned.
- Fix: in `mon_buf_init`, on `size == 0` set `end = p = buf`, `over = true` and write nothing; or delete the `resp_max > 0` guard and state `resp_max >= 1` as the handler contract.

### F9 NIT: `cmd_info`'s `if (b.over) return 0;` is redundant

- Where: `monitor_cmds.c:132`.
- Defect: removing it changes nothing observable (a full appender ignores the extra), and no test catches the removal (`mut.py` "info over ignored").
- Fix: delete it, or keep it only to skip the shim call and say so.

## Doubts verified

- Per-occurrence `!e event <type> overflow` (not latched): confirmed.
  - Every cut line gets exactly one notice, identical in C and the sim over 60 000 fuzzed lines (`diff/fuzz.py`, seeds 1 to 3, 0 mismatches).
  - Positive control: a copy with the cut starting one byte early gives 2059 mismatches.
  - Cost: about 21 bytes per cut line (8 percent of a full line), but the row count doubles.
  - SPEC 2.5 states "not latched", so no contradiction; keep it.
- -O2 growth: confirmed. M0+ -O2 core with app printf 5004 to 6224 (+1220). Without app printf, -O2 improves (7755 to 6210).
- `monitor_eventf` builds cost more: confirmed +756 with app printf (6105 to 6861).
  - Not in the Doubts: +648 without app printf too (8864 to 9512, M0+ -Os). The changelog's "2.3 KB less flash on a board with no printf" holds only if the board never calls `monitor_eventf`.
- IAR/Keil empty `MON_PRINTF`: reasoned only. `void f(const char *, ...) ;` with an empty trailing macro is valid C99. Not compiled on either toolchain.
- Vendoring projects' `monitor_eventf` calls: none exist. A grep of `~/Syncthing/auto-charger/**/*.c` outside the vendored monitor finds no calls, so the new format attribute cannot break those builds.
- The vendoring projects do link `snprintf` (`charger_control` and `charger-test` `monitor_port.c`), so the "app printf" footprint column is theirs (see Footprint).
- On-target behaviour, stack figures and Windows: not verified (no ARM emulator installed; static figures only).
- `test_cli.py` follow flake: outside this slice, not checked.

## Footprint

Object text/bss, `arm-none-eabi-gcc` 13.3, `-Os -mthumb -ffunction-sections -fdata-sections` (whole object, before gc, no libc):

| Object | M0+ before | M0+ after | M4 before | M4 after |
|---|---|---|---|---|
| monitor.o | 3679 / 988 | 4283 / 989 | 3715 / 988 | 4307 / 989 |
| monitor_cmds.o | 2124 / 80 | 2264 / 80 | 2168 / 80 | 2296 / 80 |

Linked flash/RAM the monitor adds (newlib-nano, `--gc-sections`, over an empty main; the fix batch's harness, rerun on copies):

| Build | M0+ before | M0+ after | M4F before | M4F after |
|---|---|---|---|---|
| core, no app printf | 6832 / 1488 | 4518 / 1080 | 6728 / 1488 | 4520 / 1080 |
| core, app printf | 4081 / 1076 | 4533 / 1080 | 4105 / 1076 | 4521 / 1080 |
| plot + mark, no app printf | 8832 / 1488 | 6680 / 1080 | 8776 / 1488 | 6712 / 1080 |
| plot + mark + eventf, app printf | 6105 / 1076 | 6861 / 1080 | 6165 / 1076 | 6905 / 1080 |
| plot + mark + eventf, no app printf | 8864 / 1488 | 9512 / 1492 | | |
| core, standard newlib, no app printf | 35400 / 3188 | 4520 / 1080 | | |

- The owner's three boards link `snprintf` themselves, so for them this round is about +0.45 KB flash and +4 B RAM, not a saving (owner should pick whether that is acceptable).
  - Per-symbol breakdown (M0+):
    - FIRMWARE-2 event cut: about 310 B (`event_end` 188, `event_begin` 32, `monitor_mark` +40, `plot_reject` +52).
    - FIRMWARE-17: `family_match` 76 B.
    - Appender primitives: about 170 B.
    - Converted call sites: +20 to +64 B each.
    - Removals offset part of this: `monitor_dispatch` -64, `emit_hex_resp` -48, a switch table -32.
- All figures match `fix-firmware.md`'s table within a few bytes.
- `__aeabi_uidiv`/`__aeabi_uidivmod` are gone from `monitor.o` (nm), as FIRMWARE-16 intended.

## Checked, nothing found

- Strict ARM build: 32 `MON_NO_*` combinations x `MON_CAN_BUSES` 1 and 9 x M0+ and M4, each file on its own, with `-Wall -Wextra -Wformat=2 -Wconversion -Wsign-conversion -Wshadow -Wcast-align -pedantic -Werror`: all clean.
- 32 versus 64 bit: the new code uses only `uint32_t`/`int32_t`/`size_t`, and `-Wconversion` is clean on ARM. No `long` reaches the formatter, so the int-32/long-32 versus long-64 split affects only the app's own `monitor_eventf` formats, which `MON_PRINTF` now checks.
- Appender end of buffer: every size 1 to 14 for ch/str/u32/s32, the guard byte never written, `over` exact, NUL always placed (`diff/fmt.c`, ASan/UBSan).
- `emit_dec_u32` powers of ten: 0, every power boundary, UINT32_MAX, sweep against `printf`.
- `mon_parse_dec_u32` bound: 4294967295 accepted, 4294967296/4294967299/11 digits/`+1`/`-1` refused.
- Line limit boundaries:
  - OK payload at seq 1: 249 sent, 250 is ERR 8. At seq 65535: 245 sent, 246 is ERR 8.
  - `!pd` body 249 registers and rebroadcasts at exactly 255; 250 is refused.
  - A marker of exactly 255 bytes is sent whole; 256 is cut with a notice (`diff/bnd.c`).
- `event_end` against `sim._cut_event`: 60 000 fuzzed lines identical, with a mutation control.
- In-place hex: backward encode and forward decode are overlap-safe for `out == data`. Decode writes only inside its own argv token, and `rx`/`rd` land in `g_resp`, never aliasing `tx`/`wr` in `g_line`.
- Nested `monitor_poll`: skips RX only, still drains CAN and rebroadcasts. The guard is cleared after dispatch, and no emit path holds `g_out` across a handler call. Revert caught (`mut.py`).
- Revert-verify of the round's tests: caught by the suite:
  - nested guard, filter kind, appender capacity -1, `emit_pd` over-guard;
  - scan fit, read zeroing, old `vsnprintf` size, zero printing;
  - `family_match` in the single-level loop (families build), forward hex encode, space-run trim, `emit_ok` over-guard.
- `test_families.c` "no CAN drain" negative: positive control run by hand; the same sequence with CAN enabled emits `!can 0 - 123 -`.
- `test_firmware_monitor.py`: the 6-summary guard fails on a missing or extra build. The format-check test has a positive control.
- SPEC 2.1 tokenizer: the firmware tokenizes on `' '` only (`monitor.c:991`), matching the ruling.
- SPEC 2.4 against the dispatch table: badcmd for an unknown or missing subcommand, badarg for `can0`/`can9` with a known one, `-` refused as `can tx` flags.
  - A dropped family answers nosup to every spelling, as 5.1 says.
- SPEC 5.1 x86-64 `.bss` 1300 B: confirmed (1128 + 172).
- `info` still reports `can=1` under `MON_NO_CAN`, as SPEC 2.4 line 123 allows.
- Port template and header ISR advice (`mon_can_frame_t f = {0};`) match INTEGRATION section 4.
