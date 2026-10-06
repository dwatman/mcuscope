# Fuzz and firmware footprint (aspect `fuzz`), main at 0e1b7e9

Scratch: `~/tt-data/mcuscope-2026-10-05/fuzz/` (245 MB; `diff/` 92 MB of corpora and `fw/` 131 MB of TX dumps are regenerable from the seeds below).

- `diff/gen.py`, `diff/js.mjs`: seeded protocol fuzz, Python against the web UI decoders.
- `board/run.py`, `board/cmd_fuzz.py`: socket:// fake board into a real `mcuscoped`; `board/cr_*.bin` and `cr_js.mjs` for FUZZ-1.
- `fw/fw_fuzz.c`, `fw/xcheck.py`: firmware monitor fuzz under ASan/UBSan, plus the host decoders over its output.
- `fp/`: footprint scripts reused from the 2026-09-23 round (`build.sh`, `matrix.sh`), `stack.py`, and the variants `src_<commit>`, `src_noovf`, `src_ovfhook`, `src_hooks`.

No hypothesis in the venv and no clang/libFuzzer, so every fuzzer is a seeded random generator.
Python probes run from `host/` with `uv run python <probe>`.

## FUZZ-1 HIGH CONFIRMED: the daemon decodes a line it does not store, so a line with an extra CR is decoded differently by the daemon, the web UI and the daemon after a restart

- Where: `host/mcuscope/serial_link.py:938` tokenizes `line` as received (one CR stripped at :819), while `store.py:1367` stores `fold_breaks(line)`.
  Every other decoder reads the stored, folded form: the web UI (live WS rows and backfill) and `learn_stored_plot_defs` (`serial_link.py:103`) on attach.
- Failure, for a line ending `\r\r\n` (a firmware printing `"...\r\n"` through a CRLF-translating stdio) or carrying a CR mid-line:
  - `!can 1 - 100 00\r\r\n`: the daemon's last token is `00\r`, so no `can_frames` row and a `!can decode failure` sys row; the stored raw is `!can 1 - 100 00 `, which the web UI CAN table decodes as id 0x100.
    `GET /can/frames` and `mcu can` report no frame while the UI shows it.
  - `!p 10 a=1\r\r\n` and `!ps 1 2 06\r\r\n`: no plot points (nothing in `/plot/*`, `mcu plot`, PlotJuggler); the UI charts both.
  - `!pd 0 c:u1\r\r\n`: rejected live, so `!ps 0` samples stay generic events.
    After a daemon restart the stored `!pd 0 c:u1 ` primes the cache and `!ps 0 3 07` decodes as `c=7`: the same capture decodes differently depending on when the daemon last started.
  - `!m hi\r\r\n` is accepted (`parse_marker` normalizes), so the ingest path is not even consistent with itself, nor with `protocol.parse_can_event(line)`, which accepts the same line.
- Repro: `FZ_STREAM=board/cr_stream.bin FZ_DUMP=board/cr_dump.json uv run python board/run.py 0` (rows, `/can/frames` and `/plot/channels` printed: 1 frame of 2, 1 channel of 4), `node board/cr_js.mjs` (UI decodes all four), then `FZ_KEEPDB=1 FZ_STREAM=board/cr_stream2.bin ... run.py 0` (after restart `/plot/channels` has `c` from sid 0 = 7.0).
- Fix: decode the string that is stored: fold first in `_submit_rx_line` (`line = fold_breaks(line)` before `classify`), so the daemon, the stored row, the UI and the restart prime all see one text.
  Add the `\r\r\n` and mid-line CR lines to `tests/plot_grammar_cases.json` and a CAN equivalent, driven through the ingest path, not `parse_*`.
- Class 19 (two engines validating one thing) and class 60 (judged on the raw value, the stored value normalised after).

## FUZZ-2 MEDIUM CONFIRMED: the monitor masks an out-of-range CAN id into a different valid id; SPEC 2.5 says such a frame is lost from the decoded view

- Where: `firmware/monitor/monitor.c:1038` (`f->id & (f->ext ? 0x1FFFFFFF : 0x7FF)`), pinned by `firmware/tests/test_monitor.c:1754` (`test_can_id_mask`); `docs/SPEC.md:227` says "The reference firmware does not range-check the id its driver hands it, so an out-of-range frame is lost from the decoded view".
- Failure: a shim that forgets to set `ext` for an extended frame hands over id 0x1234; the monitor emits `!can 5 - 234 07`, a well-formed standard frame of a different message.
  The host stores it as id 0x234 with no notice anywhere, where SPEC (and the host's own range check) would have kept it as a generic event with a `!can decode failure` sys row.
  The filter also matches the masked id (`can filter 234 7FF` passes 0x1234), so the wrong id is the one the user filtered for.
  The same file treats an out-of-range `bus` the opposite way: dropped and announced once (`!e can bus <n> dropped`).
- Repro: `make -C firmware/tests run`, case "can std id masked to 11 bits" (input 0x1234, output `!can 5 - 234 07`).
- Fix: treat an out-of-range id like the out-of-range bus (drop, one latched `!e can id dropped` per init), or emit it unmasked and let the host keep it as a generic event; then update SPEC 2.5:227 and INTEGRATION.md:306 to whichever holds.
  SPEC is the contract; the code changed 2026-08-28 (0b5eed9) without it.

## FUZZ-3 LOW CONFIRMED (owner pick): a build that never calls `monitor_plot`, `monitor_mark` or `monitor_eventf` links 0.5 KB and 144 B RAM it cannot use

- Where: `monitor_poll` and `monitor_init` reference the plot registry (`g_plots` 128 B, the rebroadcast loop) and the over-long-event episode (`overflow_end`, `overflow_notice`, `event_type`, `g_ovf_*`), and `event_send` checks the episode on every event.
  Only `event_end` (called from `monitor_eventf`, `monitor_mark`, `plot_reject`) can open an episode, and only `monitor_plot` can register a stream, so in a commands-and-CAN build both are dead but linked.
- Measured (app printf, nano, flash / RAM over an empty main), variant `fp/src_hooks`: `monitor_plot` and the first `event_end` install function pointers that `monitor_poll`/`monitor_init`/`event_send` call when set.

| Build | M0+ -Os | M4F -Os | M0+ -O2 |
|---|---|---|---|
| core | 4997 / 1112 → 4481 / 968 (−516 / −144) | 4977 → 4505 (−472) | 7008 → 6012 (−996) |
| plot | 7205 / 1112 → 7361 / 1132 (+156 / +20) | 7233 → 7393 (+160) | 10280 → 10364 (+84) |

- Behaviour: identical. `firmware/tests/test_monitor.c` built against the variant with ASan/UBSan passes 323/323, and `fw_fuzz` seed 1 (300k iterations) produces a byte-identical TX dump against `src` (`cmp`).
- The overflow half alone (`fp/src_ovfhook`) saves 312 B / 16 B on core and costs 80 B / 12 B on plot builds.
- Trade-off for the owner: about 0.5 KB and 13% of the monitor's RAM back for a board that only takes commands and emits CAN, against 0.16 KB on every board that plots.

## FUZZ-4 LOW CONFIRMED: the footprint figures in SPEC 5.1 and INTEGRATION.md are stale by 0.4 to 0.8 KB

- Where: `docs/SPEC.md:1284-1293`, `firmware/monitor/INTEGRATION.md:36-41`.
- Measured now (same configurations as the 2026-09-23 Footprint section, which these scripts reproduce exactly at fac0c96: 4069 / 6065 / 1076):

| | stated | measured |
|---|---|---|
| core, M0+ -Os / M4F -Os / M0+ -O2 | 4.6 / 4.6 / 6.3 KB | 5.0 / 5.0 / 7.0 KB |
| plus plot and mark | 6.8 / 6.9 / 9.5 KB | 7.2 / 7.2 / 10.3 KB |
| RAM, Cortex-M | 1080 B, +12 per bus | 1099 B of monitor symbols (1111 at two buses), 1112 B linked delta |
| `.bss` on x86-64 gcc -O2 | 1300 B | 1332 B (1160 + 172) |

- The stated figures match 4462986 (4601 / 6813 / 6256 / 9496); a597ae6 and 5f16d7c added 0.4 KB (-Os) and 0.8 KB (-O2) and 32 B RAM.
- Growth since fac0c96 for core at M0+ -Os is +928 B: the overflow-notice machinery (about 0.3 KB, see FUZZ-3), `family_match` for `can<n>` (76), `emit_err` on the appender instead of a shared `snprintf` (+76), the ERR 8 checks in `cmd_ping`/`cmd_can_stat` (+112), `POW10`/`CSWTCH` tables (72). At -O2 the appender is inlined at every call (`emit_err` 324 B, `overflow_notice` 392 B), hence +2 KB.
- Stack claims hold: worst static chain below `monitor_poll` is 324 B at M0+ -Os (`emit_pd` → `event_send` → `overflow_notice`), via a command 292 B (`monitor_dispatch` → `cmd_info`); `monitor_plot` 396 B. Shims and libc leaves excluded (`fp/stack.py`).
- Fix: restate the table and RAM line from `fp/matrix.sh`; with FUZZ-3 the core row drops back to 4.5 KB.

## FUZZ-5 LOW CONFIRMED: received bytes above 0x7F become U+FFFD with no count or notice

- Where: `serial_link.py:819`, `raw.decode("ascii", "replace")`.
- Failure: UTF-8 debug text (`25.3°C`) is stored as `25.3��C`, and baud-mismatch garbage loses the byte values that identify the mismatch.
  Nothing counts it: `rx_dropped` and the sys rows cover drops and cuts only.
  The 2026-09-23 capture leg judged this lossy by design (SPEC 2.1 lets a receiver reject >0x7F); the open point is the CLAUDE.md rule that every alteration is counted or announced, and SPEC 2.2's "debug output ... untouched".
- Repro: `board/run.py 1..4`: 49k to 57k U+FFFD stored per run, `rx_dropped` counts only the oversized lines.
- Fix (owner): either state the replacement in SPEC 2.1/2.2, or count altered lines (a per-port counter, or a latched sys row like the oversized-line one).

## Checked and fine

- Python decoders, 1.1M seeded grammar-aware lines (`diff/gen.py` seeds 1-3, 11-14, 21-26, 31): no exception from `classify`, `parse_response`, `_response_seq`, `parse_can_event`, `PlotDecoder.feed`, `parse_marker`; no non-finite point returned.
  Pools: ticks at 0, 2^32-1, 2^32, 20/21 digits, `+1`, `1_0`, Arabic-Indic digits; ids past 11/29 bits and 16 hex digits; values `1e999`, `nan`, `inf`, 400-digit literals, `4.9e-324`; prototype names (`__proto__`, `toString`) as names, types and lanes; enum values at 20/21 digits; NUL, tab, 0x1C, DEL, CR mutations.
- Python against the web UI (`diff/js.mjs`, same corpora, 76k decodable lines each): `!can` frames (tick, bus, id, ext, rtr, dlc, hex), `!p`/`!pd`/`!ps` points and values, `!m` ticks agree on every line once both sides see the same text (the only divergence found is FUZZ-1's).
  Positive control: a harness bug that fed the UI a leading-space line produced DIFF output, so the comparator fires.
- Daemon over socket:// (`board/run.py` seeds 1-4, about 4000 records each): random binary, high-byte garbage, CR/LF mixes, NUL, lines of 4094 to 70000 bytes with and without terminator, chunk sizes 1 to 30000.
  Stored rows equal a reference model byte for byte (7094 / 7700 / 6902 / 7375 rows), `rx_dropped` equals the model's oversized count exactly (186 / 183 / 186 / 197), `lines_rx` matches, writer alive, no write errors, no traceback in the daemon log.
- `/cmd` response matching (`board/cmd_fuzz.py`, 250 commands, 31 malformed reply shapes: wrong seq, leading zero, `+`, tab, 20/21-digit codes, `ERR` without name, NUL, high bytes, CR, a 5000-byte reply, two replies): every answer equals what `parse_response`/`_response_seq` predict; no 500.
- Firmware monitor under ASan/UBSan (`fw/fw_fuzz.c`, seeds 1-7, 1.9M iterations at `MON_CAN_BUSES` 1, 2, 9 and all `MON_NO_*`): no sanitizer report; every TX line is printable ASCII, LF-terminated, at most 255 bytes; every command line gets exactly one response with its seq, or none when no seq is recoverable (950k commands, 0 mismatches).
  Covered: lines 240 to 280 bytes (the 255/256 boundary), NUL/high/CR/tab bytes, 13-token lines, hex and decimal tokens at and past 32 bits, `can0`/`can10`, i2c/spi payloads at and past 128 bytes, random CAN frames (dlc and bus out of range), `monitor_plot` with random bodies, lengths and NULL defs, `monitor_mark`/`monitor_eventf` with control bytes and 320-byte text.
- Host decoders over the monitor's output (`fw/xcheck.py`, 3.8M lines): every `<` response, `!can`, `!m` and accepted `!ps` decodes; the only `!pd` the host rejects carry `*1e999`, the gap SPEC 5.2 documents.
- RX cap: a 4096-byte body is stored, 4097 dropped; the cap counts a trailing CR, so a 4096-byte body sent with CRLF is dropped (not specified either way, harmless).

## Not covered

- Windows serial ports (pyserial `win32` drain path); everything here ran over socket://.
- `rfc2217://` and native tty drain paths.
- The live browser: FUZZ-1's UI side was driven through the real `can.js`/`plots.js` in node, not a page.
- The simulator's own parser (`sim.py`) under fuzz.
- `monitor_eventf` stack through newlib-nano's `vsnprintf` (libc frames not in the call graph; SPEC's 0.45 KB not re-measured).

## The two questions

1. Least confident: FUZZ-1's restart claim, first shown only by calling `PlotDecoder.learn` in-process.
   Re-driven with a real daemon restart on the same database: `/plot/channels` gained `c` (sid 0, 7.0) after the restart and lacked it before.
2. Not thought about: the same received-versus-stored split could reach other consumers of `raw` (the `/wait` and `/assert` matchers, PlotJuggler, `mcu tail --match`).
   Matchers read the stored row, so they agree with the UI; PlotJuggler takes the daemon's decode, so it sides with `/plot`. Neither was driven separately.
