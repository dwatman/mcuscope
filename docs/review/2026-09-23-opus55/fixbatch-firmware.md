# Fix batch: firmware (2026-09-24)

HEAD: `b994076c88c875ad43c30789cda6039d147b7abe` (uncommitted working tree; other batches edited other files concurrently)

Scratch: `~/tt-data/mcuscope-2026-09-24/fixbatch-firmware/`.

- Baseline copy: `fw-base/`.
- C mutation runner: `rv.py`. Sim mutation runner: `rv_sim.py`.
- Fuzz: `fuzz3.py` (the reviewer's generator plus marker `@tick` shapes) and `evh`.
- Footprint: `fp/table.sh` and `fp/table.txt`. Strict ARM matrix: `armstrict.sh`.

## Per finding

### F1: the sim's plain filter drops extended frames

- `host/mcuscope/sim.py:301`: `if st.filter_ext != frame.ext: return None`; comment at `:66` reworded.
- Test: `test_sim.py:735` `test_can_filter_kind_x_extended_plain_standard_all_both` (renamed from `..._x_flag_passes_only_extended_frames`). It covers x, plain and all, each over both frame kinds, and asserts that the sim's traffic has both kinds.
- Revert: the old `filter_ext and not frame.ext` fails that test (`rv_sim.py`).

### F2: the uncovered formatter branches

- `firmware/tests/fake_shims.c`: ADC `neg` (raw -5, mv `INT32_MIN`) and `min` (raw `INT32_MIN`, mv -1).
- `test_monitor.c` `test_gpio_adc`: `adc read neg` gives `OK raw=-5`. `adc read min` gives `OK raw=-2147483648 mv=-1`. `gpio set led 1` then `get` gives `OK 1`.
- `test_event_overflow_cut`: 16-char type quoted, 17-char type reads `?`.
- Reviewer's 5 mutations, rerun in `rv.py`, all caught:
  - no minus: `run` fails;
  - negate via `-v`: `asan` fails only (UBSan "negation of -2147483648"); `-O2` wraps to the same bytes, so `run` cannot see it;
  - `mv` branch always on: `run` fails;
  - gpio get always `0`: `run` fails;
  - type bound 15: `run` fails.

### F3: a cut that keeps only the header sends only the notice

- `monitor.c:363-381` `event_end`: walks past the type. For type `m` it then skips spaces, and if all that is left is `@<digits>` the line is not sent.
  - The old `if (cut > 1)` case is subsumed: `if (i < cut)`.
  - `is_dec_digit` moved above `event_end`.
- `sim.py:865` `_cut_event` mirrors it. It splits the rest of the line on `' '`; for `first == "m"` a leading `re.fullmatch(r"@[0-9]+")` token is dropped; the line is sent only if tokens remain.
- SPEC 2.3 (`docs/SPEC.md:113-114`), `INTEGRATION.md` "!p" paragraph, and the `event_end` comment updated.
- C tests (`test_event_overflow_cut`):
  - bare type (`q    yyy..`) and bare marker tick (`monitor_mark` of one 290-byte token) now send only the notice;
  - `m @7x`, `m @` and `p @7` are kept, and a clockless `m cal` is kept;
  - the trim test now carries a token (`!q a`).
- Sim tests:
  - `test_sim_error_codes.py:80` `test_a_cut_that_keeps_only_the_header_sends_only_the_notice` covers the same cases;
  - `:61` updated for the trim case and the 16/17-char types;
  - `test_sim.py:723` event now `!m a ...`.
- Revert, C: 7 mutations, all caught. They are the whole revert, every type a marker, tick never skipped, any `@token` a tick, the lone-`@` guard, no space skip, and type not skipped.
- Revert, sim: 4 mutations (whole revert, every type a marker, tick never skipped, `@\S*`), all caught. All 4 fail the same test function on different assertions.
- C against sim fuzz (`fuzz3.py` seeds 1-3, and the reviewer's `fuzz2.py` seeds 1-3): 0 mismatches on 120 000 lines.
  - About 1 400 lines per seed exercise the notice-only path.
  - Positive control: the baseline C against the new sim gives 1252 mismatches (seed 1).
- While revert-verifying, two conditions in my first draft turned out to be redundant (the cut is trimmed, so anything left past `i` is a token). I deleted them rather than leave untested branches.

### F5: a frame on an undeclared bus is announced

- `can stat` counters come from the shim (`mon_can_stat`), not the monitor, so there was no monitor-owned counter to extend. I used a latched notice.
- `monitor.c:990-1008` `drain_can`: the first drop after init emits `!e can bus <n> dropped`, and later drops are silent. The latch is `g_can_bus_noted` (`:944`), reset in `monitor_init` (`:1156`).
  - It is written with `mon_buf` and `write_line`, not `event_end`. Going through `event_end` linked it into the core and cost +424 B; this way costs +84 B.
- No wire-format change the host parses (`!e` is a generic event), so `protocol.py` is untouched.
- Docs updated:
  - SPEC 2.5 (`:214`, `:321`), outside my listed sections (see Doubts);
  - `INTEGRATION.md` bus paragraph and the `monitor_init` reset list;
  - `monitor.h` pop comment and the port template comment.
- Tests (`test_can_buses`):
  - "events per bus": the notice sits between the frames;
  - "stray bus notice latched": later bus 3 and bus 200 frames are silent, with a bus-1 frame in the same poll as the positive control;
  - "re-armed by init".
- Revert: no notice, not latched, and init not re-arming are all caught.
- The sim has no driver-reported bus, so there is nothing to mirror.

### F6: the standard-newlib cost wording

- `SPEC.md` 5.1 and `INTEGRATION.md` Footprint now say: "22 to 29 KB more flash and 1.7 KB more RAM than on newlib-nano (24 to 31 KB and 2.1 KB more than a build that does not call it)".
- Figures remeasured on the final code:
  - nano against std, plot+mark+eventf: M0+ 9672 to 38236, M4F 9616 to 31373;
  - against plot+mark with no eventf: M0+ 6836, M4F 6884.

### F7: one budget, one check

- `monitor_cmds.c:88-94` `read_into_resp`: `room = min(resp_max, MON_OK_PAYLOAD_MAX + 1)`, and refuse if `2 * n >= room`.
- `:80-84` `hex_resp_in_place` lost its clamp and its `resp_max` parameter; the three callers were updated.
- `test_hex_resp_clamp` (`test_monitor.c:1243`) now tests the refusal:
  - resp[9] takes 4 bytes exactly (`06420642`), and resp[8] is ERR 8;
  - in a full buffer, 122 bytes are OK (244 digits) and 123 are ERR 8;
  - the data token is refilled before each call, since spi decodes it in place.
- Revert: the old buffer-only refusal, a budget that ignores the wire, and an off-by-one (`>`) are all caught. The off-by-one is also an ASan stack overflow.

### F8: `mon_buf_init` with size 0

- `monitor.c:198-202`: size 0 sets `end = p = buf` and `over = true`, and writes nothing. The `monitor.h` appender comment says so.
- Test: `test_zero_size_resp` (`test_monitor.c:1310`) dispatches `ping` into `z + 1` with size 0 and checks that the sentinel `"ZZ"` is intact.
- Revert: caught.
- With F7, `hex_resp_in_place` no longer carries its own `resp_max > 0` guard, so 0 is now legal everywhere.

### F9: `cmd_info`'s `if (b.over) return 0;`

- Deleted (`monitor_cmds.c:123`). It skipped the `mon_info_extra` call only for a resp too small for `up=... can=N`. That is reachable only through the internal `monitor_dispatch` (the wire buffer is 256), so it was not worth a branch.
- No branch left to revert-verify.

### Doubts-section nits

- Only one needed a change: the CHANGELOG "2.3 KB less flash" line (see CHANGELOG below).
- The rest were confirmations or not verifiable here (IAR/Keil).

## Verification

- C suite: `make run`, `asan` and `families` all green (286/286, and 6 of 6 family builds). `families-asan` is also green.
- `host/tests/test_firmware_monitor.py` 5 passed; `test_sim.py` 58; `test_sim_error_codes.py` 21; `test_sim_tcp.py` 12; `test_sim_pty.py` 3. Each file was run alone.
- `test_regressions.py -k can_filter` passed. It sits outside my files and only asserts the OK response and `filter_ext`.
- `ruff check .` is clean.
- Strict ARM build (`armstrict.sh`): 256 compiles clean.
  - The matrix is 32 `MON_NO_*` subsets x `MON_CAN_BUSES` 1 and 9 x M0+ and M4, each file alone.
  - Flags: `-Os -Wall -Wextra -Wformat=2 -Wconversion -Wsign-conversion -Wshadow -Wcast-align -pedantic -Werror`.
  - Positive control: a planted `uint8_t z = v;` fails all 128 monitor.c builds.
- `rv_sim.py` runs pytest with `--import-mode=append` against a scratch package copy, because `host/tests/__init__.py` makes the default mode load the tree's package. Its first run without the flag passed every mutation; that is what exposed the problem.

## Footprint after

Linked flash/RAM the monitor adds (newlib-nano, `--gc-sections`, over an empty main; baseline is this round's pre-fix tree):

| Build | M0+ before | M0+ after | M4F before | M4F after |
|---|---|---|---|---|
| core, no app printf | 4518 / 1080 | 4602 / 1080 | 4520 / 1080 | 4616 / 1080 |
| core, app printf | 4533 / 1080 | 4613 / 1080 | 4521 / 1080 | 4621 / 1080 |
| plot + mark, no app printf | 6680 / 1080 | 6836 / 1080 | 6712 / 1080 | 6884 / 1080 |
| plot + mark + eventf, app printf | 6861 / 1080 | 7021 / 1080 | 6905 / 1080 | 7073 / 1080 |
| plot + mark + eventf, no app printf | 9512 / 1492 | 9672 / 1492 | | |
| core, -O2 | 6210 / 1080 | 6250 / 1080 | | |
| plot + mark, -O2 | 9262 / 1080 | 9482 / 1080 | | |
| core, `MON_NO_CAN` | 3462 / 1068 | 3462 / 1068 | | |

- Where the growth comes from:
  - F5 notice: +84 B in every CAN build;
  - F3: about +76 B wherever `event_end` is linked (plot, mark, eventf);
  - F7 and F9 net slightly negative in `monitor_cmds.o`.
- Object text/bss at `-Os`, whole object:
  - `monitor.o`: M0+ 4283/989 to 4461/990, M4 4307/989 to 4495/990;
  - `monitor_cmds.o`: M0+ 2264/80 to 2246/80, M4 2296/80 to 2278/80.
- RAM unchanged: the latch bool packs into existing padding.
- SPEC 5.1 and `INTEGRATION.md` updated to these figures:
  - table: 4.6 / 4.6 / 6.3 KB and 6.8 / 6.9 / 9.5 KB;
  - family savings: CAN 1.14 KB, I2C 0.48 KB, SPI 0.11 KB; GPIO and ADC unchanged.
- The -O2 saving stays at "1.6 to 2.6 KB".

## Proposed CHANGELOG lines

- Firmware: an over-long event whose cut would keep only its type (or a marker's `@tick`) now sends only its `!e event <type> overflow` notice, never a bare `!m @7`; the simulator matches.
- Firmware: a received CAN frame on a bus above `MON_CAN_BUSES` is announced once per init as `!e can bus <n> dropped`, no longer dropped without a trace.
- Firmware: an i2c/spi read whose hex answer would not fit the response budget is refused with `ERR 8`, never answered with fewer bytes (internal `monitor_dispatch` callers only; unreachable over the wire).
- Simulator: a plain `can filter <id> <mask>` passes only standard frames, as the firmware and SPEC 2.4 do; it used to pass extended ones too.
- Correct the existing line 37 ("2.3 KB less flash and 0.4 KB less RAM on a board with no printf"). It should read "2.2 KB less flash and 0.4 KB less RAM on a board with no printf that does not call `monitor_eventf`":
  - with `monitor_eventf` linked, the round costs +648 B (the reviewer's figure);
  - this batch took the core from 4518 to 4602 B against the old 6832.

## Not done

- F4 (`cli.py` AI_GUIDE): not mine. It may also want the new `!e can bus <n> dropped` beside `!e event p overflow`.
- CHANGELOG.md: not edited, per brief.
- No existing test outside my files needed an edit.
- On-target behaviour, IAR/Keil builds, Windows: not verified.
- The whole Python and JS suites: not run, per brief.

## Doubts

- F5 SPEC edit is in 2.5 (`:214`, `:321`), outside the 2.3/2.4/5 sections I was given. That is where the undeclared-bus drop and the `!e` notice list live, and leaving 2.5 saying "dropped" with an incomplete notice list seemed worse. Each edit is one anchored line; revert them if another batch owns 2.5.
- F5 notice form and latch are my choice, since the brief left them open:
  - wording `!e can bus <n> dropped`;
  - once per init across all buses, so only the first stray bus number is named.
  - A per-bus latch needs a 256-bit map for a `uint8_t` bus, which did not seem worth it.
- F3 follows the brief literally: only `m` skips a `@tick`. So a cut `!p` that keeps only its tick (`!p 123`) is still sent; the host stores it as a generic event.
- Rounding: the M0+ -O2 core is exactly 6250 B, written as 6.3 KB.
