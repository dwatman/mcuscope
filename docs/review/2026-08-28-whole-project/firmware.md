# Firmware leg - review round 2

HEAD: `fd76735 POST /ports held to the config-write bar` (as expected).

Scope: `firmware/monitor/` (monitor.h, monitor.c, monitor_cmds.c, port_template/, INTEGRATION.md, README.md) and `firmware/tests/` (test_monitor.c, fake_shims.c, Makefile).
Contract read: `docs/SPEC.md` sections 2.1-2.5 and 5.1-5.4.

## What was run

All builds and probes ran in `/tmp/claude-1000/review-r2/fw/`, on copies of the sources.
Nothing in the repo was edited and no build artifact was created inside it.
`firmware/tests/test_monitor` and `firmware/tests/test_monitor_asan` were already present from 2026-08-16, are gitignored, and their mtimes are unchanged; `git status --porcelain` is empty. There is nothing for the orchestrator to clean.

| Run | Result |
|---|---|
| Repo suite, `-std=c99 -Wall -Wextra -Werror -O2` | 153/153 pass |
| Repo suite, ASan + UBSan `-fno-sanitize-recover=all` | 153/153 pass, no reports |
| `-Wconversion -Wsign-conversion -Wshadow -Wcast-qual -Wstrict-prototypes -Wmissing-prototypes -Wpointer-arith` on both .c files | clean |
| `-std=c99 -pedantic` on both .c files | clean |
| `.bss` measured (1096 + 172 = 1268 bytes) | matches the SPEC 5.1 figure exactly |
| Hostile-port probe harness, 15 groups, ASan + UBSan | 3 memory-safety defects found (below) |
| Fuzz soak: 40000 structured command lines, 20000 random-byte bursts up to 1200 bytes, 200000 random plot bodies, ASan + UBSan | clean, no reports |
| Fuzz soak: 400000 near-valid plot bodies (18004 accepted, longest emitted line 232 of a 257-byte buffer), ASan + UBSan | clean, no reports |
| `make arm-check` | not verifiable, `arm-none-eabi-gcc` is not installed here |

The line assembler, the token splitter, the 12-token cap, the 255/256-byte boundary, the seq range, hex and decimal overflow guards, the CAN DLC clamp, tick wraparound, and the `!ps` hot-path encoder were all attacked directly and all held.
The findings below are the ones that did not.

## Findings

### 1. HIGH, CONFIRMED - `emit_ok` reads past `g_resp` when a handler leaves its payload unterminated

`firmware/monitor/monitor.c:238`

Invariant broken: the module never bounds its read of the handler-supplied `resp` buffer, and no part of the contract requires that buffer to be NUL-terminated.

`monitor.h:92-95` and SPEC 5.2 say only "write the OK payload into `resp` (no `OK` prefix, no newline)" and describe `resp_max` as "the buffer size".
`process_line` sets `g_resp[0] = '\0'` before dispatch, which reads as the monitor tracking termination itself.
`emit_ok` then does `snprintf(g_out, sizeof g_out, "<%lu OK %s\n", seq, resp)`, an unbounded `%s`.

A handler that does `memcpy(resp, data, n)` with `n == resp_max`, or `strncpy(resp, s, resp_max)` (which does not terminate on truncation), walks off the end.

Probe: a registered handler doing `memset(resp, 'Q', resp_max)`.

```
==2724899==ERROR: AddressSanitizer: global-buffer-overflow ... READ of size 257
    #4 in emit_ok ../monitor/monitor.c:238
    #7 in monitor_poll ../monitor/monitor.c:840
0x... is located 0 bytes after global variable 'g_resp' defined in '../monitor/monitor.c:34' of size 256
0x... is located 32 bytes before global variable 'g_out' defined in '../monitor/monitor.c:33' of size 257
```

Failure scenario: on a target this reads straight through `g_resp` into `g_out` and whatever `.bss` follows, and emits it on the debug UART as an `OK` payload until a NUL turns up. The write side is bounded by `snprintf`, so it is a disclosure and corruption bug, not a smash.
The suite's own `h_longresp` handler terminates correctly, so nothing in the repo exercises this.

### 2. HIGH, CONFIRMED - `cmd_info` reads past its 64-byte `extra` buffer when `mon_info_extra` does not NUL-terminate

`firmware/monitor/monitor_cmds.c:83-87` (the `snprintf` is line 86)

Invariant broken: same class as finding 1, but here the unterminated buffer comes from a **declared shim in the module's own contract**, not from application code.

`monitor.h:164`, SPEC 5.3, `port_template/monitor_port_template.c:144-147` and INTEGRATION.md all declare `int mon_info_extra(char *buf, size_t max)` with no statement that the shim must NUL-terminate. The signature reads exactly like `snprintf`'s size argument, so a port author filling `max` bytes is inside the documented contract.

Probe: a shim doing `memset(buf, 'Z', max); return 0;`.

```
==2719124==ERROR: AddressSanitizer: stack-buffer-overflow ... READ of size 65
    #4 in cmd_info ../monitor/monitor_cmds.c:86
    #5 in monitor_dispatch ../monitor/monitor_cmds.c:413
    #6 in process_line ../monitor/monitor.c:778
This frame has 1 object(s): [32, 96) 'extra' (line 83) <== Memory access at offset 96 overflows this variable
```

Failure scenario: `info` is the first command the smoke checklist runs (INTEGRATION.md step 5), so a port that gets this wrong hits it immediately, on the stack, with adjacent frame contents going out on the wire.
`fake_shims.c` deliberately omits `mon_info_extra`, so the repo suite never runs this path at all.

Findings 1 and 2 share one root cause and one fix shape: the contract has to state "must be NUL-terminated" (and the code should bound the read anyway, since the shim is third-party by design).

### 3. HIGH, CONFIRMED - `drain_can` hands the shim an uninitialised frame and emits whatever it does not fill

`firmware/monitor/monitor.c:674` declares `mon_can_frame_t f;` with no initialisation; `emit_can_event` (`monitor.c:645-668`) then reads `f->tick_ms`, `f->ext` and `f->rtr` unconditionally.

Invariant broken: nothing in `monitor.h:151`, SPEC 5.3 or INTEGRATION.md requires `mon_can_rx_pop` to write every field. The INTEGRATION.md example fills all of them only because it copies a whole queued struct; a shim that reads a bxCAN or FDCAN mailbox directly and sets just id/dlc/data is the obvious, contract-conformant way to write it.
`cmd_can_tx` (`monitor_cmds.c:100`) does `memset(&f, 0, sizeof f)` for exactly this reason; the RX path does not.

Probe: a shim setting only id/dlc/data, with the stack pre-dirtied by a same-depth callee.

```
wire: !can 2756260743 x 123 ABCD
```

Both the tick and the `x` flag are stack garbage. Expected, had the frame been zeroed: `!can 0 - 123 ABCD`.

Failure scenario: silent, sustained data corruption with no diagnostic anywhere.
The bogus tick puts every frame at a random point on the timeline.
The spurious `x` changes the declared id width, and SPEC 2.5 says the host "decodes only an id that fits the width the flags declare", so an 11-bit frame flagged extended still decodes, and a genuine wide frame flagged standard drops out of the decoded view into generic events.
`fake_shims.c:121-127` copies a fully zeroed queue entry, so the suite cannot see this.

### 4. MED, CONFIRMED - `parse_plot_body` does not enforce SPEC 2.5 name uniqueness, so the firmware registers a `!pd` the host will refuse

`firmware/monitor/monitor.c:434-503`

SPEC 2.5: "Within one line, names must be unique. A `!p` naming the same field twice, or a `!pd` whose channel and lane names collide, is malformed: the sample is stored as a generic event, the definition is rejected."
SPEC 2.5 also: "A firmware must not emit a `!pd` body it has not validated against this grammar."
SPEC 5.2 and `monitor.h:124` claim the body is validated against "the whole 2.5 channel-spec grammar", with "one gap remains by design" (the `*1e999` scale exponent). This is a second, undeclared gap.
The parser's own comment at `monitor.c:430-433` states the intent it misses: "a body the host refuses is registered forever and its samples land as generic events, with nothing visible on the target but a 0 return."

Probe:

```
body "ax:s2 ax:s2 ay:s2"     -> rc=0, wire: !pd 0 ax:s2 ax:s2 ay:s2 / !ps 0 10 0000,0000,0000
body "gpio:u1:/gpio,irq"     -> rc=0, wire: !pd 1 gpio:u1:/gpio,irq / !ps 1 11 00
```

Both should be `MONITOR_ERR_BADARG`.

Failure scenario: exactly the one the comment describes. The stream registers, the host rejects the definition, every `!ps` for that sid lands as a generic event forever, and the 5 s rebroadcast re-asserts the bad definition. The target reports success.
Note that a duplicate name **across** streams is explicitly not enforceable by either side ("Nothing enforces this"), so only the within-one-body case is a defect.

### 5. MED, CONFIRMED - INTEGRATION.md contradicts the code on kind-sigil validation

`firmware/monitor/INTEGRATION.md:323-325`:

> The monitor's body parser does not care: it only reads the two-character `<type>` token right after the field's first `:` to compute that field's byte width, then skips ahead to the next space. Everything after the type, including a `=...`/`/...` kind sigil, rides through into the emitted `!pd` line untouched, so no firmware code change is needed to use either kind.

The code does the opposite. `valid_field_tail` / `valid_enum_body` / `valid_bits_body` (`monitor.c:390-428`) validate the entire tail and return `MONITOR_ERR_BADARG` on anything the host would refuse, which `test_plot_body_grammar` (test_monitor.c:657-686) asserts across 19 cases.

Failure scenario: a reader following the guide expects any tail to ride through, so a mistyped label, a 17-character label, a `*scale` on an enum, or a 9th lane on a `u1` comes back as a bare `MONITOR_ERR_BADARG` from `monitor_plot` with the guide saying that cannot happen.
This is stale text left over from before the grammar validation landed. The paragraph should say the tail is fully validated and name `MONITOR_ERR_BADARG` as the outcome.

### 6. MED, CONFIRMED - a port with `tick_ms == NULL` registers plot streams that are never rebroadcast

`firmware/monitor/monitor.c:557` and `monitor.c:845` both fall back to `now = 0` when `tick_ms` is absent, so `plot_rebroadcast`'s `(uint32_t)(now - s->last_pd_ms) >= 5000` is permanently false.

Probe: register a stream on a port with `.tick_ms = NULL`, then poll 100000 times.

```
first sample: !pd 0 a:u1 / !ps 0 0 07
after 100000 polls, rebroadcast bytes = 0
```

Invariant broken: SPEC 2.5 - "The firmware re-emits `!pd` for each active stream roughly every 5 s, so a late-joining consumer (or restarted daemon) is blind for at most that long."
With no clock that bound is infinite: any daemon attaching after the first sample decodes nothing for that stream, ever.

The module null-checks `tick_ms` on every use, i.e. it half-supports a clockless port, and `monitor_mark` has an explicit documented degradation for that case (emit `!m` with no `@tick`). The plot path has no such degradation and no refusal. Either reject a clockless port at `monitor_init`, or rebroadcast on a poll count when there is no clock. `monitor_port_t` also does not mark `tick_ms` mandatory, while INTEGRATION.md:9-10 calls the three callbacks "the only mandatory glue" - pick one and say it in both places.

### 7. MED, CONFIRMED - `emit_err` can put a negative error code on the wire

`firmware/monitor/monitor.c:227-233` formats the handler's return with `%d` and no range check.

Probe: an application handler returning `-5`, then `4242`.

```
wire: <1 ERR -5 internal
wire: <2 ERR 4242 internal
```

Invariant broken: SPEC 2.3 - "An emitter uses only these codes." SPEC 2.1 allows a leading `-` on a decimal token only "where a negative value is meaningful", which an error code is not.

The out-of-table positive code is tolerable (SPEC 2.3 explicitly says a receiver "accepts any decimal code and reports it with the name the line carried"). The negative one is not: it is off-grammar in a direction no receiver is told to expect.

`monitor.h:93` says only "Return 0 for OK, or a `MONITOR_ERR_*` code", and a handler wrapping a driver that returns `-EIO` or `-1` is the natural way to get there. Clamp a non-zero code outside 1..9 to `MONITOR_ERR_INTERNAL` in `emit_err`.

### 8. MED - the test doubles are gentler than a real port, and hide three of the findings above

`firmware/tests/fake_shims.c`

- `fake_uart_read` (lines 68-74) returns `min(avail, max)` correctly, so the clamp at `monitor.c:835` is **never exercised**, despite SPEC 5.4 stating it as a requirement and the Makefile comment (lines 42-46) recording it as a real found defect. I drove it with a shim returning `n + 8` and one returning `SIZE_MAX`: the clamp holds, but that is my probe saying so, not the suite.
- `mon_can_rx_pop` (lines 121-127) copies a fully zeroed queue entry, hiding finding 3 completely.
- `mon_spi_xfer` and `mon_info_extra` are deliberately absent (line 5-6), so the weak default answers `nosup` and the suite never runs either handler's data path. That is where finding 2 lives, and it is the only handler carrying two 128-byte stack buffers plus the longest possible response. With a real shim the max-length case works: a 254-byte command line yields 119 payload bytes and a 244-byte response, well inside the limit.
- `mon_i2c_xfer` (lines 145-162) always fills all `rd_len` bytes. A shim that returns 0 having filled fewer leaves `cmd_i2c_rd`'s `uint8_t rd[64]` (`monitor_cmds.c:243`) partly uninitialised and `emit_hex_resp` puts the tail on the wire. Probed: `>1 i2c rd 48 8` with a 1-byte-filling shim emits `<1 OK 1100000000000000`. Same for `cmd_spi_xfer`'s `rx[128]` (`monitor_cmds.c:284`), probed as `<2 OK 55440000`. Unlike finding 3 the shim contract here does say "Fill `rx` with `len` MISO bytes" for SPI (INTEGRATION.md:198), so this is a contract the port owns; for `mon_i2c_xfer` nothing equivalent is stated.
- `test_can_queue_bounds` (test_monitor.c:783-797) tops the fake queue out at 32 frames, below `drain_can`'s `guard < 64` bound, so the 64-frame-per-poll limit INTEGRATION.md:128 promises is untested. Probed with a 200-frame source: exactly 64 emitted per poll, 136 left queued. It works; it is just unasserted.
- `fake_feed` / `fake_feed_raw` (lines 44-54) and `fake_uart_write` (lines 76-84) `memcpy` into `rxbuf[4096]` / `txbuf[16384]` with no bounds check. A future test that feeds more than that overruns a static in the harness itself, and the failure would read as a monitor defect.

### 9. LOW, CONFIRMED - a plain unit slot accepts any byte, and `write_line` then silently mangles the emitted `!pd`

`firmware/monitor/monitor.c:418-420` - "plain display unit: no charset rule either side" - returns true for any bytes up to the field's end, including 0x80+ and control bytes. `write_line` (`monitor.c:203-217`) then rewrites them to `.` on the way out.

Probe:

```
body "a:u1:\xC2\xB5V"  (UTF-8 micro sign)  -> rc=0, wire: !pd 0 a:u1:..V
body "a:u1:\x01\x02"                       -> rc=0, wire: !pd 1 a:u1:..
```

No forgery risk (`write_line` covers that), but the definition on the wire differs from the one the application declared, with a 0 return. A unit of `µV` becomes `..V`. SPEC 2.1 constrains the whole protocol to 7-bit printable ASCII in both directions, so `parse_plot_body` should refuse a non-printable unit rather than emit a mangled one.

### 10. LOW - the command parser accepts control bytes 0x01-0x1F and 0x7F mid-line

`firmware/monitor/monitor.c:750-758` rejects only NUL and bytes above 0x7F.

SPEC 2.1 says "Encoding: 7-bit printable ASCII", both directions. SPEC 5.4 says only "a 13th token, a byte above 0x7F, or an embedded NUL fails the whole line". The code follows 5.4; the two SPEC clauses disagree with each other.

Probed: `>2 ping\x7f` gives `ERR 1 badcmd` (the DEL is inside the token), `>3 pi\x07ng` gives `ERR 1 badcmd`, and `>4 gpio set \x1b[31mled 1` passes an ANSI escape sequence through into `mon_gpio_set`'s name argument.

Impact is small: output is sanitized regardless, and control bytes only reach shim name lookups where they fail to match. Worth resolving as a SPEC edit (5.4 wins, 2.1 gets a "the command parser additionally tolerates non-printable low bytes" clause) rather than a code change, unless the intent really was to reject them.

### 11. LOW - INTEGRATION.md's CAN ring example

`firmware/monitor/INTEGRATION.md:226-253`

- `f.tick_ms = g_systick_ms;` (line 235) references a symbol declared `static` in a different example, four sections earlier (line 92). Copied as printed, it does not compile.
- The ring is correct on a single-core Cortex-M: both the array and the indices are `volatile`, C forbids reordering volatile accesses relative to each other, and ISR entry is a context synchronisation point. It is **not** sufficient on a dual-core part (an M7+M4 H7, or an M33+M0 pairing) where the producer runs on the other core, which needs a `DMB` between the payload store and the index publish. INTEGRATION.md:99-101 is careful about `volatile` for exactly this class of bug and stops one step short of saying where `volatile` alone runs out.

## Checked and found correct

Recording these so a later round does not re-derive them.

- Line assembler at 254 / 255 / 256 content bytes, and the overflow-then-valid-command sequence: all correct, and the 255-byte case fits `g_line[MONITOR_LINE_MAX + 1]` exactly.
- Overflow recovery: an all-digit 300-byte line stays silent (the truncated seq overflows `mon_parse_dec_u32`); a `>1` plus 300 spaces gives `<1 ERR 8 overflow`.
- Token cap: 12 tokens including the seq dispatch, 13 gives `ERR 2 badarg`; `tokenize` writes at most index 11 of `char *tok[12]`.
- Seq range: 0, 65536, `-1` and `4294967297` are all silently dropped; `00001` normalises to 1; 65535 works.
- `mon_parse_hex_u32` / `mon_parse_dec_u32` overflow guards both hold.
- Tick arithmetic: `(uint32_t)(now - last)` handles wrap; probed across the 2^32 boundary with no spurious or missed rebroadcast. `emit_dec_u32`'s `char tmp[10]` is exactly enough for 4294967295, `emit_hex_u32`'s `tmp[8]` exactly enough for 32 bits.
- `!ps` encoding: zero-padded uppercase big-endian per field, unpadded hex tick, commas between fields, per SPEC 2.5. Verified against `a:u1 b:u2 c:u4 d:f4` at tick 0xFFFFFFFF.
- The `mon_plot_line_fits` compile-time bound on the unchecked hot path is sound, and 400000 near-valid bodies produced a longest line of 232 bytes against a 257-byte buffer.
- RTR DLC is emitted as one decimal digit, clamped at 8 (`monitor.c:662`), as SPEC 2.5 requires of a sender.
- `can tx` DLC acceptance of `08` and `0000000004` is **conformant**, not a defect: SPEC 2.4 sanctions receiver leniency here explicitly and names this exact firmware behaviour.
- `can filter` rejects `r` with `ERR 2 badarg` and matches on `(id & mask)` only, per SPEC 2.4.
- `can tx` id range: 0x7FF standard, 0x1FFFFFFF extended, both boundaries enforced.
- `emit_ok`'s overflow boundary is exact: 255 content bytes send, 256 answers `ERR 8`.
- `i2c scan` clamps to `MON_OK_PAYLOAD_MAX` and truncates on a whole token under the stuck-SDA case.
- Reentrancy within one dispatch is safe: a handler calling `monitor_eventf` / `monitor_plot` uses `g_out` before `emit_ok` formats into it, never during.
- `monitor_dispatch` cannot be shadowed by an application command; builtins are matched first at both levels.
- `.bss` is 1268 bytes, matching the SPEC 5.1 figure to the byte.
