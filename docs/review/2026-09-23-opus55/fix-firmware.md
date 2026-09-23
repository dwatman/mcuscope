# Fix batch: firmware (2026-09-23)

Scratch, probes and measurement scripts: `~/tt-data/mcuscope-2026-09-23/fix-firmware/` (`mutate.py`, `pymut.py`, `fp/measure.sh`, `fp/matrix.txt`, `fwprobe.c`).

## Misrouted follow-up

The watermark follow-up was meant for the panes batch and has been re-routed there; I did not touch its files.

## Proof method for the footprint refactor (FIRMWARE-15, 16, 18)

- Baseline: `fwprobe.c` (the reviewer's fuzz harness) built against an untouched copy, `FWDUMP=1 fuzz` seeds 1-3 at 150k iterations, bus counts 1/2/9: 9 dumps, about 4.5M wire lines.
- Stage A (appender, division-free decimal, in-place decode, format attribute, family flags) was applied first: all 9 dumps `cmp` identical to the baseline.
- Final: the finished code with only FIRMWARE-2's `event_end` and FIRMWARE-8's `ext` test reverted is again `cmp` identical on all 9. The only wire-visible changes are those two.
- The final code under `-fsanitize=address,undefined`: `fwprobe fuzz` seeds 1/2/3/11 at 200k, bus counts 1/2/9, 12 runs, no fault, no invariant break.

## Per finding

**FIRMWARE-1** (docs): `mon_can_frame_t f = {0};` in both ISR examples (`INTEGRATION.md:276`, `:332`), one paragraph on whole-struct copies (`:302-303`), same rule in `monitor.h` and the port template.
Docs only; nothing to test.

**FIRMWARE-2**: `monitor.c:329` `event_end` cuts an over-long event back to its last space (the byte past the limit counts as a boundary), then emits `!e event <type> overflow`.
`<type>` is the first token, or `?` when empty or over 16 chars; a line with no space is not sent, only the notice.
All events route through it: `monitor_eventf` (`:851`, now `vsnprintf` into 256 bytes), `monitor_mark` (`:879`), `plot_reject`.
Simulator mirrors it (`sim.py:865 _cut_event`).
Tests: `test_monitor.c:640 test_event_overflow_cut` (20-cell `!p`, exactly 255, cut exactly at the limit, space run, long type, marker), `test_eventf` one-token case; `test_sim_error_codes.py` (same cases, plus a real `mark` command).
Revert: old truncation, cut starting one byte early, trim removed, 16-char bound removed, notice removed: each caught. Simulator: truncation, bound 255, no rstrip, unbounded type: each caught.
Host: `!e ...` is stored as a generic `event` row (no decoder claims `!e`); `mcu lines --match "^!e"` finds it. No host code change needed.

**FIRMWARE-3**: `monitor.c:45` `g_in_dispatch`, set around the dispatch (`:1075`); a nested `monitor_poll` skips RX (`:1131`) but still drains CAN and rebroadcasts. Documented in `monitor.h` header comment, SPEC 5.2, INTEGRATION section 3.
Test: `test_monitor.c:721 test_nested_poll` (pipelined `>1 wait abc`, `>2 wait xyz`, `>3 ping` each answered once, argv intact; positive control: a queued CAN frame is emitted from inside the handler).
Revert: guard removed, flag never set: both caught.

**FIRMWARE-4** (host half): `protocol.py:914` a non-finite analog value is skipped; `:916` a sample left with no point is `None`. `_decode_field` no longer gates f4, so the post-scale check is the one load-bearing check (HEALTH-16).
Tests: shared fixture cases with a new `points` key (below), `test_protocol.py test_typed_f4_drops_a_non_finite_point_only`, `test_review_r2_sim.py test_non_finite_typed_value_drops_that_point_only` (through the daemon: `amps` stored, `volts` not).
Revert: whole-sample drop, check removed, empty sample kept: each caught.
pjstream.py: see the addendum at the end.

**FIRMWARE-5**: `sim.py:224` checks the `can` subcommand before the bus digit; `:364` `spi`, `:393` `adc` answer badcmd for an unknown or missing subcommand. SPEC 2.4 says so.
Test: `test_sim_error_codes.py` (11 badcmd cases, 5 badarg cases). Revert of each of the three: caught.

**FIRMWARE-6**: `protocol.py:577` `parse_can_tx_args` refuses `-`. SPEC 2.4 says so.
Tests: `test_protocol_tokenizer.py test_can_tx_flags_refuse_the_event_dash`, `test_sim_error_codes.py`. Revert: caught.

**FIRMWARE-7**: `sim.py:908` an over-long ERR keeps its code and drops its detail; an over-long OK is still ERR 8.
Test: `test_sim_error_codes.py test_an_error_detail_never_turns_the_code_into_overflow` (the reviewer's 255-byte `F` line). Revert: caught.

**FIRMWARE-8**: `monitor_cmds.c:33` filter slot stores `ext`, `:45` a mask filter passes only its frame kind, `:225` set from the `x` flag. RAM unchanged (`mode` became `uint8_t`). SPEC 2.4 and INTEGRATION say so.
Test: `test_monitor.c test_can_filter` (`x` passes only extended, plain only standard, `all` both, `none` neither kind). Revert: ext ignored, ext not stored: both caught.

**FIRMWARE-9 / HEALTH-19** (Python half): every parser in `protocol.py` now uses `split_tokens` (runs of U+0020 only): `parse_response`, `parse_can_event`, `parse_plot_adhoc`, `parse_plot_def`, `decode_plot_sample`, `PlotDecoder.learn/feed/points`; `parse_marker` reads its tick token after spaces only (`:1106`).
Tests: new `test_protocol_tokenizer.py` (CAN, responses, marker tick, decoder entry points, tab/VT/FF/0x1C-0x1F), fixture cases below. Revert of each of the 9 call sites: caught.
The live ingest path splits in `serial_link.py`, not here: see Not done.

**FIRMWARE-10**: `monitor.h:52` `MON_PRINTF` (GCC/Clang only), `:113` on `monitor_eventf`; casts in `monitor.h`, SPEC 2.5, INTEGRATION.
Test: `test_firmware_monitor.py test_monitor_eventf_arguments_are_format_checked` (`"p %s"` with an int fails to compile; positive control compiles).
Revert: attribute removed from a scratch header, the bad call compiles (the test would fail).

**FIRMWARE-12**: SPEC 2.4 `ERR 1 unknown` is now `ERR 1 badcmd`.

**FIRMWARE-13, 14**: re-measured with the CubeIDE arm-none-eabi-gcc 13.3 (`fp/matrix.txt`); SPEC 5.1 and INTEGRATION section 1 rewritten with the table, RAM, stack, nano.specs requirement, `-Os` advice and flag savings. Figures below.

**FIRMWARE-15**: `monitor.h:209` `mon_buf_t` appender, `monitor.c:196` implementation; every internal `snprintf` replaced (`emit_err`, `emit_ok`, `emit_pd`, `plot_reject`, `monitor_mark`, ping, info, can stat, i2c scan, gpio get, adc read). `monitor_cmds.c` no longer includes `<stdio.h>`.
Tests: the existing 247 checks plus the fuzz dump proof. Revert: OK overflow ignored, resp read unbounded (ASan), scan fit check off by one, `emit_pd` over-guard (new check `plot over-long rebroadcast not sent`, `test_monitor.c:559`): each caught.

**FIRMWARE-16**: `monitor.c:107` powers-of-ten `emit_dec_u32`, `:187` constant overflow bound in `mon_parse_dec_u32`.
Tests: `test_monitor.c:753 test_parse_dec_bounds` (0, 4294967295, leading zeros, 4294967290, 4294967296, 4294967299, 11 digits, empty, junk). Revert: bound off by one, zero printing nothing: both caught.

**FIRMWARE-17**: `-DMON_NO_CAN`, `_I2C`, `_SPI`, `_GPIO`, `_ADC`; each swaps its rows for one `{family, NULL, cmd_nosup}` row (`monitor_cmds.c:466`), matched through `family_match` (`:555`) so `can2 tx` and `can0` are nosup too. `MON_NO_CAN` also drops the drain (`monitor.c:916`, `:1145`) and filter.
Test: new `firmware/tests/test_families.c`, Makefile `families` / `families-asan` (6 builds each: every flag and all five), wrapper `test_firmware_family_flags` and `..._under_sanitizers` with a 6-summary count guard (verified: 5 builds fail the guard).
Revert: `strcmp` instead of `family_match`, drain left in: both caught.

**FIRMWARE-18**: I2C/SPI write data decoded in place in its argv token; reads land in `resp` (zeroed first, `read_into_resp` refuses a read larger than `resp`), then `hex_resp_in_place`. `mon_hex_encode` walks backwards so `out == data` works.
Frames: `cmd_spi_xfer` 280 to 40 B, `cmd_i2c_wrrd` 232 to 56, `cmd_i2c_wr` 152 to 32, `cmd_i2c_rd` 104 to 48 (M0+ -Os).
Tests: new checks `i2c read larger than resp refused` / `spi xfer larger than resp refused` (`test_monitor.c:1183`), existing short-read and echo checks. Revert: forward encode, no zeroing, fit check disabled (ASan): each caught.

## Shared fixture cases added (for the JS batches)

`host/tests/plot_grammar_cases.json` (the JS driver currently passes all of them):

- def, all `valid: false`: `!pd 3 c:s1 \u001fdZ:u4`, `!pd 0\u001ca:u1`, `!pd 0 a:u1\u001db:u1`, `!pd 0 a:u1\tb:u1`.
- adhoc: `!p  10   a=1  b=2` valid; `!p\u001f100 a=1`, `!p 100\u001ea=1`, `!p 100 a=1\u001d`, `!p 100 a=1\u001cb=2`, `!p 100 a=1\tb=2` invalid.
- sample, decodes with a `points` list: `a:f4 b:u2` / `7FC00000,0001` gives `["b"]`; `a:u2 b:f4 c:u1` / `0001,FF800000,02` gives `["a","c"]`; `a:f4*1e300 b:u1` / `7F7FFFFF,02` gives `["b"]`; `!ps  0  1  01,02` gives `["a","b"]`.
- sample, does not decode: `!ps 0 1\u001f01`, `!ps\u001c0 1 01`; the three single-channel non-finite cases keep `decodes: false` with a new `why`.
- New optional key `points`: channel names the decoded sample carries, in order. `test_plot_grammar_fixture.py` asserts it; `webui_js/plot_grammar.test.mjs` does not yet (panes: add the same assertion).

Marker tick rule for chrome (JS-6): the tick is `@<digits>` as the first token after runs of U+0020 only; `!m \t@5 hi` has no tick (Python `parse_marker`).

## Existing tests edited

- `firmware/tests/test_monitor.c test_eventf`: the 300-char case now expects `!e event ? overflow` (FIRMWARE-2).
- `firmware/tests/test_monitor.c test_registry_limits`: fills 3 slots, not 4, since `main` registers the new `wait` handler.
- `host/tests/test_protocol.py test_typed_f4_refuses_non_finite`: renamed `..._drops_a_non_finite_point_only`, asserts the per-point rule.
- `host/tests/test_protocol.py test_typed_sample_refuses_a_post_scale_infinity`: two-channel definition, asserts the finite channel survives.
- `host/tests/test_protocol.py test_format_and_parse_agree_over_the_whole_can_domain`: omits the flags token when there are no flags (`-` was never a `can tx` token).
- `host/tests/test_review_r2_sim.py test_non_finite_typed_sample_is_a_generic_event`: renamed `..._value_drops_that_point_only`, asserts `amps` stored and `volts` not.
- `host/tests/test_sim.py test_an_oversized_response_is_answered_overflow_not_truncated`: the event half expects the cut and notice.
- `host/tests/test_firmware_monitor.py`: `_assert_all_checks_ran` takes a summary count; new family-flag and format-check tests.
- `host/tests/test_plot_grammar_fixture.py`: asserts the optional `points` key.

## SPEC edits

- 2.1: a receiver splits on runs of U+0020 only.
- 2.3: the over-long event rule replaces "Event lines are truncated to the limit".
- 2.4: badarg applies only to a known subcommand; unknown or missing is badcmd whatever the digit; `ERR 1 unknown` is `ERR 1 badcmd`; `-` is not a `can tx` flags token; `x` filter passes only extended frames, plain only standard, all/none unchanged.
- 2.5: cast in the `!p` example; the non-finite point rule; the `!e event <type> overflow` notice.
- 3.7: non-finite values never reach a datagram because the decoder drops the point.
- 5.1: measured footprint table, RAM, stack, nano.specs, family flags and savings; the stale 4 KB / 1268 B figures removed.
- 5.2: `MON_PRINTF` in the API block and on `monitor_eventf`; nested `monitor_poll` rule.
- 5.3: build flags give nosup without linking; "stack residue" is now "residue".

## Measured (arm-none-eabi-gcc 13.3, newlib-nano, gc-sections, linked total over an empty main)

| Build, flash / RAM | before | after |
|---|---|---|
| M0+ -Os core, no app printf | 6832 / 1488 | 4518 / 1080 |
| M0+ -Os plot, no app printf | 8832 / 1488 | 6680 / 1080 |
| M0+ -Os core, app printf | 4081 / 1076 | 4533 / 1080 |
| M0+ -Os full (app calls `monitor_eventf`), app printf | 6105 / 1076 | 6861 / 1080 |
| M0+ -O2 core, app printf | 5004 / 1076 | 6224 / 1080 |
| M0+ -Os core, standard newlib | 35400 / 3188 | 4520 / 1080 |

- M4F -Os tracks M0+ within 50 B. `MON_CAN_BUSES=2`: +48 B flash, +12 B RAM.
- Flags (M0+ -Os, app printf, core 4533): CAN -1056 B and -12 B RAM, I2C -464, GPIO -152, ADC -148, SPI -88, all five 2281 B.
- Stack: worst monitor chain about 300 B below `monitor_poll` (was 688); `monitor_eventf` about 440 B below its caller (newlib-nano prologues).
- x86-64 `.bss` 1300 B (SPEC said 1268).

## Public API and build-flag changes (for re-vendoring)

- `monitor.h`: new `MON_PRINTF(fmt, arg)` macro; `monitor_eventf` declared with `MON_PRINTF(1, 2)`, so a mismatched format/argument is now a `-Wformat` warning (an error under `-Werror`) in vendoring projects.
- `monitor.h` internal section: new `mon_buf_t`, `mon_buf_init`, `mon_put_ch`, `mon_put_str`, `mon_put_strn`, `mon_put_u32`, `mon_put_s32`; `mon_hex_encode` and `mon_hex_decode` documented as in-place safe.
- New opt-in flags `MON_NO_CAN`, `MON_NO_I2C`, `MON_NO_SPI`, `MON_NO_GPIO`, `MON_NO_ADC`; default build unchanged.
- Behaviour: over-long events are cut at a space plus a notice; a mask CAN filter now honours `x` (a plain filter no longer passes extended frames); a nested `monitor_poll` skips RX.
- Stack frames of `cmd_spi_xfer` and `cmd_i2c_*` shrank; `resp` now doubles as the shim's read buffer (shims still get separate `tx` and `rx`).
- Check before re-vendoring: any vendoring project that calls `monitor_eventf` with uncast `uint32_t` will now warn, and any that relies on a plain `can filter` passing extended frames changes behaviour.

## Changelog

- Firmware: an over-long event (`!p`, marker) is cut at its last space and followed by `!e event <type> overflow`, instead of being cut mid-number.
- Firmware: `can filter <id> <mask> x` passes only extended frames, a plain filter only standard frames.
- Firmware: a command handler may call `monitor_poll()` while it waits.
- Firmware: no `snprintf` in the monitor; 2.3 KB less flash and 0.4 KB less RAM on a board with no printf, and no float printf from standard newlib unless `monitor_eventf` is used.
- Firmware: opt-in `-DMON_NO_CAN/_I2C/_SPI/_GPIO/_ADC` build flags.
- Firmware: `monitor_eventf` arguments are checked against the format string on GCC/Clang.
- Host: a non-finite typed value drops that point only; the rest of the sample is stored.
- Host: plot, CAN, response and marker lines split tokens on spaces only, as the firmware does.
- Simulator: answers `ERR 1 badcmd` for unknown `spi`/`adc`/`can<N>` subcommands, refuses `-` as `can tx` flags, and no longer turns a long unknown command into `ERR 8`.

## Not done

- **Link batch, `serial_link.py:893`**: the live ingest path splits with `line.split()` and hands the tokens to `parse_can_event_tokens` and `points_from_tokens`, so the tokenizer ruling does not reach live ingest until it reads `parts = p.split_tokens(line)`.
  `serial_link.py:198` (`_response_seq`) should use `p.split_tokens(norm[1:])` for the same reason.
- **Panes**: add the `points` assertion to `webui_js/plot_grammar.test.mjs` (`if (c.points) assert.deepEqual(decodePlotSample(c.line, def).points.map(p => p.name) ...)`, adapted to the JS sample shape).
- **Chrome**: marker tick rule above (JS-6).
- `/tmp/tmp.nMY1BQF4F5` is not deleted yet: a recursive delete needs the owner's explicit confirmation (global CLAUDE.md), which a coordinator message cannot give.
  It is one throwaway ARM build from my first measurement run, all mine, 10 files, about 200 KB: `a.elf`, `e.elf`, `ld.log`, `main.o`, `main.su`, `map.txt`, `monitor.o`, `monitor.su`, `monitor_cmds.o`, `monitor_cmds.su`.
  Command once confirmed: `rm -rf /tmp/tmp.nMY1BQF4F5`.

## Doubts

- Least sure: the `!e event <type> overflow` form. It fits the `!e <subsystem> <detail>` grammar and mirrors `!e plot <sid> badarg <why>`, but it is not latched, so a firmware emitting an over-long `!p` at 100 Hz doubles its line count. I judged per-occurrence the honest reading of "nothing is lost silently".
- The -O2 cost grew: +1.2 KB flash at M0+ -O2 with an app printf (inlined appender calls). The docs recommend `-Os` for the two files; I did not try to shape the code for -O2.
- `monitor_eventf` builds cost more than before where the app has printf (+0.76 KB M0+ -Os), 0.19 KB of it `event_end`.
- Not checked: on-target behaviour (all stack figures are static), IAR/Keil (the `MON_PRINTF` empty expansion is untested there), the vendoring projects' own `monitor_eventf` calls against the new format check, and Windows.
- `test_cli.py` had two `follow` failures in one full-file run that passed alone and on rerun; the cli batch was editing those files at the time.

## Addendum: pjstream.py (orchestrator decision, 2026-09-23)

- `pjstream.py:152`: the per-value non-finite filter and its `math` import are deleted. Since the decoder drops a non-finite point (SPEC 2.5), the filter could not be reached from the live path.
- Test: `test_plotjuggler.py test_non_finite_values_dropped_not_emitted` (which called `send` directly with `inf`/`nan`) is replaced by `test_non_finite_f4_field_is_left_out_of_the_datagram`.
  It sends `!pd 0 ok:f4 bad:f4 worse:f4` / `!ps 0 64 3FC00000,7FC00000,FF800000` through `PlotDecoder.points` into `send`, parses the datagram with a parser that rejects NaN/Infinity tokens, and asserts `{"ok": 1.5}`.
  It also asserts that an all-non-finite sample yields no points, so nothing is sent.
- Revert-verified in a scratch copy (`pymut.py PJ`):
  - With the whole-sample drop restored in `protocol.py`, the test fails with `TimeoutError` because no datagram arrives.
  - With the finiteness check removed, it fails with `ValueError: non-JSON token NaN`.
- `test_plotjuggler.py` passes 46/46 and ruff is clean.
- Changelog: no separate line; covered by "a non-finite typed value drops that point only".
