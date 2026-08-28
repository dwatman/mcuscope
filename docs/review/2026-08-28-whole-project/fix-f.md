# Fix batch F (firmware) - report

Scope touched: `firmware/monitor/{monitor.c,monitor.h,monitor_cmds.c,INTEGRATION.md,port_template/monitor_port_template.c}`, `firmware/tests/{test_monitor.c,fake_shims.c}`, `host/tests/test_firmware_monitor.py`, `docs/SPEC.md` section 5 only.
Nothing committed. Makefile unchanged (no change was needed).

## Gates

| Gate | Result |
|---|---|
| `make` + `./test_monitor` (`-std=c99 -Wall -Wextra -Werror -O2`) | 200/200 checks passed |
| `make asan` (ASan + UBSan, `-fno-sanitize-recover=all`) | 200/200 checks passed, no reports |
| `uv run python -m pytest tests/test_firmware_monitor.py` | 2 passed |
| `ruff check tests/test_firmware_monitor.py` | clean |
| `.bss` (`size` on both monitor objects, gcc -O2 x86-64) | 1096 + 172 = **1268 bytes, unchanged**; SPEC 5.1 needs no edit |
| `make arm-check` | not runnable here (`arm-none-eabi-gcc` absent), as in the review leg |

Checks went 153 -> 200. `.bss` held at 1268 only because the new poll counter was placed beside
`g_tx_dropped`; declared next to `g_plots` the same 4-byte object cost 32 bytes of alignment padding
(measured both ways).

## What each item became

1. **FW1** `emit_ok` formats the payload with `%.*s` bounded at `sizeof g_resp`. `monitor.h` and SPEC 5.2
   now state the handler payload must be NUL-terminated.
2. **FW2** `cmd_info` calls `mon_info_extra(extra, sizeof extra - 1)` and writes `extra[sizeof extra - 1] = '\0'`
   after the call. Contract line in `monitor.h`, SPEC 5.3, a new INTEGRATION.md "Info extras" subsection, and the
   port template comment.
3. **FW3** `drain_can` zeroes the frame before every `mon_can_rx_pop`, like `cmd_can_tx`. Documented in
   `monitor.h`, SPEC 5.3 and INTEGRATION.md: unfilled fields read as zero.
4. **FW4** `parse_plot_body` enforces SPEC 2.5 within-body uniqueness over channel names plus bit lanes,
   pairwise, via a stack-only name iterator (no new statics) -> `MONITOR_ERR_BADARG`.
5. **FW5** INTEGRATION.md's kind-sigil paragraph rewritten: the tail is fully validated and
   `MONITOR_ERR_BADARG` is named.
6. **FW6** clockless ports rebroadcast `!pd` every `MON_PLOT_PD_POLLS` (10000) polls. The degradation is
   stated in `monitor.h` (on the `tick_ms` member), SPEC 5.2 and INTEGRATION.md section 2, beside
   `monitor_mark`'s.
7. **FW7** `emit_err` clamps any non-zero code outside 1..9 to `MONITOR_ERR_INTERNAL`; stated in `monitor.h`
   and SPEC 5.2.
8. **FW8** harness hardened: over-reporting `uart_read` shim (both `max + 8` and `SIZE_MAX`); CAN pop with a
   partial-fill mode; real `mon_spi_xfer` and `mon_info_extra` shims incl. the max-length response (254-byte
   command line, 119 payload bytes, 245-byte response) and buffer-filling modes; `i2c rd`/`wrrd`/`spi xfer`
   receive buffers zeroed by the monitor with contract lines in INTEGRATION.md and SPEC 5.3; CAN queue raised
   to 256 with the drain asserted at exactly 64 per poll over four polls (64/64/64/8); bounds `assert`s in
   `fake_feed`, `fake_feed_raw` and `fake_uart_write`.
9. **FW9** a plain unit slot is refused unless every byte is 0x20..0x7E.
10. **RG-F15** `emit_can_event` masks the id to the declared flag width (11/29 bits), beside the dlc clamp;
    INTEGRATION.md notes the shim still owns id validity.
11. **RG-F16** `monitor_mark` refuses text whose first word is an `@<digits>` tick sigil, but only on a
    clockless port - the exact condition `format_marker` uses (`tick_ms is None`). With a tick of its own the
    monitor's `@<tick>` leads and the same text round-trips fine, so refusing it there would reject a legal marker.
12. **FW11** the CAN ring example names where `g_systick_ms` comes from, and a new sentence says `volatile`
    suffices single-core only, a dual-core producer needing a `DMB` on both sides of the index publish.
13. **R30** both wrappers parse `<n>/<m> checks passed` out of stdout and fail unless the summary exists,
    `m != 0` and `n == m`, mirroring `test_webui_js.py`.

## Revert-verify evidence

Each fix reverted in place, both builds rerun, then restored (`/tmp/claude-1000/review-r2/fw-revert.py`).

| Fix reverted | Plain | ASan/UBSan |
|---|---|---|
| FW1 `%.*s` -> `%s` | 198/198 (invisible, as the leg reported) | `AddressSanitizer: global-buffer-overflow` past `g_resp` |
| FW2 headroom + terminate | FAIL `info extra fills its buffer` | `AddressSanitizer: stack-buffer-overflow` |
| FW3 `drain_can` memset | FAIL `can partial fill zeroed`, `can partial fill zeroes rtr` | clean (stale values, not a memory error) |
| FW4 uniqueness (forced `return true`) | FAIL all 5 duplicate cases | same |
| FW6 clockless `!pd` | FAIL `clockless rebroadcast on poll count` | same |
| FW7 code clamp | FAIL `negative err code clamped`, `out-of-table err code clamped` | same |
| FW8a `uart_read` clamp | driver produces no summary (walks off `g_stage`) | UBSan `index 64 out of bounds for type 'uint8_t [64]'` |
| FW8d i2c zeroing | FAIL `i2c short read reads as zeros` | clean (ASan does not track uninit) |
| FW8d spi zeroing | FAIL `spi short fill reads as zeros` | clean, same reason |
| FW9 unit charset | FAIL all 3 non-printable unit cases | same |
| RG-F15 id mask | FAIL `can std id masked to 11 bits`, `can ext id masked to 29 bits` | same |
| RG-F16 marker sigil | FAIL `clockless mark forged tick rc`, `... silent` | same |
| R30: driver prints `0/0 checks passed` and exits 0 | pytest: 2 failed (previously would have passed) | - |

## Judgement calls, for the orchestrator

- **`monitor_mark` is now `int`, not `void`.** RG-F16's ruling names `MONITOR_ERR_BADARG` as the refusal, which
  a `void` function cannot express, and the call is not a wire command so there is no `ERR 2` to emit. Changed
  the signature (0, or `MONITOR_ERR_BADARG` for NULL/empty and the forged-tick case) and updated SPEC 5.2,
  `monitor.h` and INTEGRATION.md. Source-compatible: every existing caller ignores the return.
- **FW8(b) deviates from the ruling's wording, deliberately.** A fake `mon_can_rx_pop` that "poisons the struct"
  *writes* `ext`/`rtr`/`tick_ms`, so no amount of zeroing in `drain_can` can help and FW3 becomes untestable
  (confirmed: with the poison in, the fixed monitor still emitted `!can 2779096485 xr ...`). The fake now writes
  only the fields a mailbox-reading shim has and touches nothing else; the test dirties the stack at the same
  depth beforehand, which makes the monitor's `memset` load-bearing and the revert deterministic.
- **A weak-default coverage note.** Defining `mon_spi_xfer` and `mon_info_extra` in `fake_shims.c` leaves no shim
  to the weak defaults. Their disabled mode answers `MONITOR_ERR_NOSUP`, so the existing `ERR 7` expectations
  still hold, but they now prove "a shim that says nosup" rather than "the weak default in monitor_cmds.c".
  Cheap to restore if wanted (drop the strong `mon_can_filter` fake, whose return value `cmd_can_filter` ignores).
- **FW11's tick variable** is referenced by a comment, not a declaration: section 2 declares `g_systick_ms`
  `static` in the same `monitor_port.c`, so an `extern` in the CAN snippet would be a conflicting declaration
  when both are copied.
- **FW10 not touched** (SPEC 2.1/5.4 wording); it is not in this batch's list and 2.1 is another agent's section.
- **Downstream**: `charger-test` vendors `monitor.c`, `monitor_cmds.c`, `monitor.h` and `INTEGRATION.md`; all four
  changed, and `monitor_mark`'s signature is the one change a vendored copy will notice.
