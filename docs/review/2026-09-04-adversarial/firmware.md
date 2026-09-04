# Adversarial review leg: `firmware/monitor` and `firmware/tests`

- HEAD: `7a1120f`
- Scope: `firmware/monitor/{monitor.h,monitor.c,monitor_cmds.c,port_template/monitor_port_template.c,INTEGRATION.md}`, `firmware/tests/`, against `docs/SPEC.md` sections 2 and 5.
- Registry classes swept: 41 (callee-filled memory read beyond the contract) and 22 (stdlib predicate standing in for a wire grammar).

## Method

1. Read `docs/REVIEW.md` classes 41 and 22, then SPEC 2.1-2.5 and 5.1-5.4, then every byte-handling path in the two `.c` files.
2. Compiler sweep, monitor sources only, no `-Werror` so warnings are collected rather than fatal:
   `gcc -std=c99 -Wall -Wextra -Wconversion -Wsign-conversion -Wshadow -Wcast-qual -Wpointer-arith -O2 -I firmware -DMON_CAN_BUSES=2 -c monitor.c monitor_cmds.c`
   Result: **zero warnings**. The added `-Wconversion -Wsign-conversion` over the shipped `-Wall -Wextra -Werror` find nothing.
3. Repo suite under sanitizers: `cd host && uv run python -m pytest tests/test_firmware_monitor.py -q` -> 2 passed (the wrapper runs `make run` and `make asan`); the ASan/UBSan binary reports 237/237 checks.
4. Independent adversarial driver (`/tmp/fwrev/adv.c`, not in the tree) with its own port and its own strong shims, so shim misbehaviour is under test control. Every emitted line is checked against a global wire invariant in the `uart_write` fake: ends in LF, at most 256 bytes, no byte outside 0x20-0x7E before the LF. Drives the brief's list: 255/256-byte lines, empty line, CR only, bare `>`, CRLF split across two `uart_read` calls, CR mid-line, NUL mid-line, 0xFF in the body and inside the seq, `99999999999`, `-1`, `+1`, `0`, `08`, `FFFFFFFFF`, `0x`-prefixed and bare `0x`, seq `0`/`65536`/`+1`/`00001`, 12 and 13 tokens, seq with no command, `can0`/`can1`/`can2`/`can3`, `can filter ... r` and `... xx`, odd-length hex, 9 data bytes, address `0x80`, extra args on every argc-blind handler, a TX ring that rejects every line, a shim that leaves `*state` untouched and one that sets it NULL, `i2c scan` with all 112 addresses ACKing at seq 65535, the longest `spi xfer` a 255-byte command line can carry, a handler payload at 245/246/250 bytes at both seq widths, `!pd` redefinition mid-stream, 17 fields, a 16 x u4 worst-case sample line, a 249-char body, a sid outside `'0'..'9'` twice (latch), a length-mismatch rollback followed by a valid call, a 600-char `monitor_eventf` and `monitor_mark`, and a marker containing an embedded LF.
5. Two fuzzers under `-fsanitize=address,undefined -fno-sanitize-recover=all`:
   - byte fuzz through `monitor_poll` (`/tmp/fwrev/adv.c` section 13);
   - grammar-aware plot-body generator plus 0-3 random mutations (substitute, truncate, delete) with the sample length swept 0..64 so every parseable body actually registers and emits (`/tmp/fwrev/plotfuzz2.c`): 120000 bodies, **28529 accepted and sampled**, 205587 lines emitted, zero sanitizer reports, zero wire-invariant violations.
6. Test quality: five assertions attacked, three mutations built and run against a scratch copy under `/tmp/fwrev/mut` (the tree was never modified; the copy was restored from `firmware/` between mutations).

No crash input was found. Nothing in the module reads out of bounds under ASan on any input driven above.

---

## Findings

### F1. `can stat` prints a shim-owned `const char *` with no NULL guard (MED)

`firmware/monitor/monitor_cmds.c:200-206`.

`cmd_can_stat` pre-initialises `state = "active"` (which correctly covers a shim that returns 0 without touching it, and is verified below), then passes whatever the shim left there straight to `%s`. The contract obliges nothing: `monitor.h:157-158` says only `state = current`, and `INTEGRATION.md:282` names three literals without forbidding NULL. A shim whose `bxcan_state_string()` returns NULL for an unrecognised register value (the shape of the example at `INTEGRATION.md:329`) reaches `snprintf("%s", NULL)`. glibc prints `(null)`; several minimal embedded printfs shipped on Cortex-M (mpaland/eyalroz `printf`, tinyprintf, some newlib-nano configurations) dereference it and hard-fault inside the monitor's response path.

This is the one unguarded `%s` in the module. Every sibling is already covered: `err_name()` always returns a literal, `emit_ok` bounds `resp` with `%.*s`, `cmd_info` terminates `extra` itself, and `cmd_ping` explicitly guards the same shape one file over with `(p && p->name) ? p->name : "monitor"`. `state` is the sibling that was missed.

Trigger: a `mon_can_stat` that returns 0 having set `*state = NULL`, then `>1 can stat\n`. Observed on this build: `<1 OK rx=1 tx=2 err=3 state=(null)`.

Registry: **class 41**. The caller pre-initialises, which the class accepts, but pre-initialisation only covers "the callee did not write"; it does not cover "the callee wrote a value the contract does not exclude". Belt and braces is explicitly required where the callee is third-party by design, which every shim is.

Fix (`monitor_cmds.c`, in `cmd_can_stat`, immediately after the `code != 0` return):

```c
	if (state == NULL) {
		state = "active";   // a shim may answer 0 and leave a NULL behind; %s must not see it
	}
```

Test that fails without it: add a `fake_can_stat` mode that sets `*state = NULL` and returns 0, then

```c
	// A shim that answers 0 with a NULL state must not reach the response's %s.
	fake_can_stat_set_mode(FAKE_STAT_NULL_STATE);
	expect_cmd("can stat null state", ">1 can stat\n", "<1 OK rx=0 tx=0 err=0 state=active\n");
```

which today emits `state=(null)` on glibc and faults on a strict libc.

**Requires re-vendoring** to charger-test, charger_control and relay_control.

### F2. The integration guide never tells the porter to clear the USART error flags (MED)

`firmware/monitor/INTEGRATION.md:39-57` ("The three mandatory port callbacks", `uart_read`).

The guide specifies the ring, the DMA-versus-IRQ choice, `volatile` on the ring indices (`:106`) and line atomicity on TX (`:83-85`), but says nothing about ORE / FE / NE / PE. On an STM32 USART, an overrun that is never cleared leaves ORE latched and **RXNE stops firing**: the monitor goes permanently deaf with `uart_read` returning 0 forever, `monitor_poll` still running, TX still working, and no diagnostic anywhere. A serial BREAK (the brief's "BREAK arriving mid-line") sets FE and typically delivers a 0x00 byte, so on a port that pushes the error byte into the ring the in-flight line is correctly rejected with `ERR 2 badarg` (verified: the NUL path is tested and works), but on a port that never clears the flag the link dies silently instead.

The monitor core is correct here; the failure is entirely in the port the guide teaches people to write, and it is the failure mode that passes every smoke test and then bricks the link on the bench under load.

Registry: **new class candidate** - *an integration guide that specifies the happy-path callback and omits the peripheral error state that silently disables it*. Distinct from class 12 (healthy-while-dead surfaces) in that nothing in this repo reports the health; it is the porter's ISR that has to.

Fix: two lines in the `uart_read` section after the ring paragraph:

```markdown
Clear the USART error flags (`ORE`, `FE`, `NE`, `PE`) in the same ISR, unconditionally.
An uncleared `ORE` latches `RXNE` off on most STM32 parts, so one overrun (or a BREAK, which also sets `FE`) makes the monitor permanently deaf while `monitor_poll` keeps running and TX keeps working: `LL_USART_ClearFlag_ORE(USART1)` and friends, or read `ISR` then write `ICR`.
```

Test: documentation only, no test.

**Requires re-vendoring** (INTEGRATION.md is inside the vendored directory), though nothing downstream breaks by not taking it.

### F3. `monitor_mark` returns 0 for whitespace-only text the host will not store as a marker (LOW)

`firmware/monitor/monitor.c:770-773`.

`monitor.h:102-104` documents the return contract as `MONITOR_ERR_BADARG` "for text that emits nothing: empty or NULL". The guard is `if (!text || !*text)`, which is emptiness of the C string, not emptiness of the marker. `monitor_mark("   ")` returns 0 and puts `!m @1000    ` on the wire; `parse_marker` (`host/mcuscope/protocol.py:1048-1051`) strips and returns `None` on empty text, so the daemon files it as a generic `event` row rather than a `marker` row. It misses the marker channel, the full-width divider and the marker exports - exactly the outcome the return code exists to signal, reported as success.

Trigger, verified on this build:

```
clocked   monitor_mark("   ") rc=0 -> !m @1000
clockless monitor_mark("   ") rc=0 -> !m
```

A tab is not affected: `write_line` rewrites it to `.`, which is a valid one-character marker.

Registry: **class 22, second face** ("a value that parses but is not a quantity"). `*text != '\0'` reads like validation of "does this produce a marker" and is not, in the same way `try: float(x)` reads like validation and is not.

Fix (`monitor.c`, replacing the current guard in `monitor_mark`):

```c
	if (!text) {
		return MONITOR_ERR_BADARG;
	}
	const char *t = text;
	while (*t == ' ' || *t == '\t') {
		t++;   // the host strips the text, so whitespace-only emits no marker at all
	}
	if (*t == '\0') {
		return MONITOR_ERR_BADARG;
	}
```

Test that fails without it (drop into `test_mark`):

```c
	// A marker the host would file as a plain event is not a marker: refuse it here.
	reset_all();
	check_int("mark whitespace-only badarg", monitor_mark("   "), MONITOR_ERR_BADARG);
	check("mark whitespace-only emits nothing", fake_tx(), "");
```

**Requires re-vendoring.**

### F4. `emit_hex_resp` clamps against the caller's buffer, not the wire budget (LOW, latent)

`firmware/monitor/monitor_cmds.c:53-57`.

`monitor.h:28-31` defines `MON_OK_PAYLOAD_MAX` as "the largest OK payload a command handler can return and still fit on the wire" and instructs "clamp any variable-length payload to this". `cmd_i2c_scan` does exactly that (`:221-223`). `emit_hex_resp`, which produces the payload for `i2c rd`, `i2c wrrd` and `spi xfer`, clamps to `(resp_max - 1) / 2` instead: 127 bytes at the shipped `resp_max` of 256, which is 254 hex characters, nine over the budget.

Two consequences, both latent today:

- A payload of 246 to 254 characters is sendable behind a short seq and answered `ERR 8 overflow` behind a long one, so the same command succeeds or fails depending on the seq the daemon happened to assign. Verified directly through `monitor_dispatch`: a 246-character payload is `OK` at seq 1 and `ERR 8` at seq 65535.
- A clamp that fires is a **silently short hex payload**, which SPEC 2.3 forbids in as many words ("a cut hex payload cannot be distinguished from a short one"), with `i2c scan` named as the single exception.

Unreachable over the wire at present, and I confirmed why rather than assuming: the request line is itself bounded at 255 content bytes, so the longest `spi xfer` a host can send is `>65535 spi xfer c <236 hex chars>` = 118 bytes, response line 247 bytes. `i2c rd`/`wrrd` are bounded at 64 bytes by their own range check. The gap only opens if `MON_MAX_DATA`, the line limit, or a direct `monitor_dispatch` caller changes.

Registry: **new class candidate** - *a length budget enforced against the local buffer rather than against the frame that has to carry it*. The named constant for the correct bound already existed and was used correctly 170 lines away.

Fix (`monitor_cmds.c`, first lines of `emit_hex_resp`):

```c
	if (resp_max > MON_OK_PAYLOAD_MAX + 1) {
		resp_max = MON_OK_PAYLOAD_MAX + 1;   // + 1 for the NUL: clamp to what the wire can carry
	}
```

Test that fails without it (extends the existing `test_hex_resp_clamp`, which calls `monitor_dispatch` directly for exactly this reason):

```c
	// The clamp is the wire budget, not the buffer: a 64-byte read at the full buffer
	// size must stay inside MON_OK_PAYLOAD_MAX so emit_ok never has to reject it.
	char big[MONITOR_LINE_MAX + 1];
	rc = monitor_dispatch(4, argv, big, sizeof big);
	check_int("hex clamp rc full buffer", rc, 0);
	check_int("hex clamp respects wire budget", strlen(big) <= MON_OK_PAYLOAD_MAX, 1);
```

**Requires re-vendoring** if applied.

### F5. `ping`, `info` and `can stat` accept any argument count (LOW)

`firmware/monitor/monitor_cmds.c:82-83`, `:90-91`, `:193-194`.

All three discard `argc`. `>1 ping extra args here`, `>1 info x y z` and `>1 can stat junk junk` all answer `OK` (verified). Every other handler in the file pins its count exactly and answers `ERR 2 badarg` otherwise, and SPEC 2.3 defines code 2 as "wrong argument count/format/range".

This is receiver leniency, which SPEC 2.4 endorses in general terms ("strict about what you send, lenient about what you accept"), so it is defensible as-is. It is filed because the inconsistency is invisible from the code: three `(void)argc` casts read as "this command takes no arguments", not as a deliberate tolerance, and the next handler added by copying one of them inherits it silently.

Registry: no class. Either add the checks or add one line to each `(void)argc` saying the extra tokens are tolerated on purpose. My preference is the comment: tightening it is a behaviour change three downstream projects would have to re-vendor for a case no host sends.

Test if tightened: `expect_cmd("ping rejects extra args", ">1 ping x\n", "<1 ERR 2 badarg\n")`.

**Requires re-vendoring** only if tightened.

---

## Sweep verdict lists

### Class 41: every callee-filled output in the shim and handler contracts

Enumerated mechanically from the declarations in `monitor.h:62-72` (port) and `:141-168` (shims), plus the handler typedef at `:88-89`. 12 sites.

| # | Output | What the contract obliges | Verdict |
|---|--------|---------------------------|---------|
| 1 | `uart_read(buf, max)` return | "bytes copied" | **complies** - clamped to `sizeof g_stage` at `monitor.c:1005`, tested for `max + 8` and `SIZE_MAX` |
| 2 | `uart_write` return | bool | **complies** - only tested for truth |
| 3 | `tick_ms()` | any uint32 | **complies** - wrap-safe subtraction at `monitor.c:621` |
| 4 | `port->name` | nothing stated | **complies** - NULL-guarded at `monitor_cmds.c:85`; over-long name yields `ERR 8`, not truncation |
| 5 | `mon_can_rx_pop(f)` fields | "only the fields it has" | **complies** - `memset` before every pop (`monitor.c:836`), `dlc` clamped to 8 before indexing `data`, `bus` 0 mapped to 1 and `> MON_CAN_BUSES` dropped, `id` masked to the width the flags declare |
| 6 | `mon_can_stat` `*rx/*tx/*err` | nothing stated | **complies** - pre-initialised to 0 |
| 7 | `mon_can_stat` `*state` | nothing stated | **VIOLATES** - F1 |
| 8 | `mon_i2c_xfer` `rd[0..rd_len)` | "must fill all when returning 0" | **complies** - `uint8_t rd[64] = {0}` at both call sites, and the monitor zeroes as documented |
| 9 | `mon_spi_xfer` `rx[0..len)` | "must fill all when returning 0" | **complies** - `uint8_t rx[MON_MAX_DATA] = {0}` |
| 10 | `mon_gpio_get` `*level` | nothing stated | **complies** - `bool level = false` |
| 11 | `mon_adc_read` `*raw`, `*mv` | `*mv = INT32_MIN` if n/a | **complies** - both pre-initialised to exactly those values |
| 12 | `mon_info_extra(buf, max)` | "must NUL-terminate within max" | **complies** - handed `sizeof extra - 1`, `extra[63]` forced to NUL by the caller, `extra[0]` pre-set |
| 13 | `monitor_handler_t` `resp` | "NUL-terminated" | **complies** - `emit_ok` bounds the read with `%.*s` at `sizeof g_resp`, which is exactly the buffer size, and `g_resp[0]` is pre-cleared |

`mon_plot_def_t.body` and `monitor_eventf`'s `fmt`/varargs are **exempt**: they come from the application in the same translation unit as the `monitor_init` call, not from a shim, and are at the same trust level as the format string itself.

### Class 22: wire grammars in the C

`grep -n "isdigit\|isalnum\|isxdigit\|isspace\|atoi\|atol\|strtol\|strtoul\|sscanf\|ctype" monitor.c monitor_cmds.c port_template/*.c` returns **0 lines**. Every token from the wire goes through an explicit character set:

| Value | Parser | Verdict |
|-------|--------|---------|
| seq | `mon_parse_dec_u32` + explicit `1..65535` | **complies** - explicit `'0'..'9'`, explicit overflow guard; `0`, `65536`, `+1`, `4294967297` and `99999999999` all rejected (verified) |
| `i2c` addr, `can` id, `can` mask | `mon_parse_hex_u32` | **complies** - `hexval()` table, explicit `UINT32_MAX >> 4` guard, optional `0x` per SPEC 2.1, bare `0x` rejected |
| `i2c rd`/`wrrd` count | `mon_parse_dec_u32` + explicit `1..64` | **complies** |
| RTR dlc | `mon_parse_dec_u32` + explicit `> 8` | **complies** (`08` accepted; SPEC 2.4 states this asymmetry on purpose) |
| hex data payloads | `mon_hex_decode` -> `hexval()` | **complies** - odd length and non-hex both rejected |
| `can` flags | explicit `'x'`/`'r'` loop | **complies** |
| `gpio set` level | `strcmp` against `"0"`/`"1"` | **complies** |
| `can filter` mode | `strcmp` against `"all"`/`"none"` | **complies** |
| bus selector digit | explicit `'0'..'9'` in `family_match`, range in `can_bus_of` | **complies** - `can0` and `can3` (at `MON_CAN_BUSES=2`) both `ERR 2` |
| plot names, enum labels, scales, units, bit lanes | hand-written `valid_*` predicates over explicit character sets | **complies** - and `valid_field_tail` refuses a non-ASCII unit rather than letting `write_line` mangle it |
| line bytes | explicit `== '\0' || > 0x7F` | **complies** with SPEC 2.1, including the clause that low control bytes and 0x7F are *tolerated* |

No violations. This module is the cleanest instance of class 22 in the repo, because it never had a permissive stdlib parser available to reach for.

### Wire-invariant sweep

Every line the module emitted across the adversarial driver, the byte fuzz and 205587 plot lines was checked for: terminates in LF, at most 256 bytes total, no byte outside 0x20-0x7E before the LF. **Zero violations.** The measured extremes match the SPEC bounds exactly: `i2c scan` with all 112 addresses ACKing at seq 65535 is 253 bytes, a 245-byte handler payload at seq 65535 is 256 bytes, a 600-character `monitor_eventf` and `monitor_mark` are both truncated to 256, the 16 x u4 worst-case `!ps` line is 159 bytes against the 159-byte compile-time bound in `MON_PLOT_WORST_LINE`.

### Signed/unsigned `char` sweep (the brief's "name an expression whose result differs")

I could not produce one, and this is a positive result rather than a gap. The candidates:

- `hexval(char c)`, `is_dec_digit(char c)`, `valid_plot_name`, `valid_enum_label`, `mon_parse_dec_u32`, `mon_parse_hex_u32`: all are range tests bounded on **both** sides against ASCII literals. For byte 0xFF, signed char gives -1 (below `'0'`) and unsigned gives 255 (above `'9'`); both fail, both return the same rejection.
- `write_line` and `valid_field_tail`'s unit scan cast to `unsigned char` explicitly before comparing.
- `process_line`'s byte check reads `g_line`, which is `uint8_t[]`, so it is unsigned on every target.
- `-Wconversion -Wsign-conversion` are silent on both files.

The one place plain `char` semantics could have mattered, `parse_plot_body`'s `char t0 = colon[1]`, compares only against `'u'`/`'s'`/`'f'`/`'1'`/`'2'`/`'4'`, which are positive in both representations.

---

## Test quality

Five assertions, each with a mutation of `monitor.c`/`monitor_cmds.c` it would not catch. Three were built and run against a scratch copy under `/tmp/fwrev/mut`; the tree was not modified.

**T1. `test_hex_resp_clamp` (`test_monitor.c:944-955`), `check("hex clamp whole bytes", resp, "0642")` - VERIFIED SURVIVING.**
Mutation: `emit_hex_resp`'s `(resp_max - 1) / 2` -> `resp_max / 2`, an off-by-one that overflows the response buffer at any even `resp_max`. **237/237 still pass.** The test picks `char resp[5]`, and at 5 the two formulas both give 2, so the only assertion guarding this clamp is evaluated at the one size where the bug is invisible. Fix: repeat the case at `char resp[6]`, where correct gives 2 bytes and the mutant gives 3 (4 hex characters plus a NUL into a 6-byte buffer is still in bounds, so ASan does not save it either; assert `strlen(resp) == 4`).

**T2. `test_overflow` (`test_monitor.c:201-221`), `check("overflow with seq", ..., "<42 ERR 8 overflow\n")` - VERIFIED SURVIVING.**
Mutation: drop `*seq <= 65535` from `recover_seq` (`monitor.c:893-894`), leaving only `*seq >= 1`. **237/237 still pass.** The overflow path is only ever driven with seq 42. `test_parser_overflow` covers an out-of-range seq, but on a normal-length line, which goes through `process_line`'s own check instead. The two range checks are separate code with one test between them. Fix: add an over-length line with seq `4294967295` and assert silence.

**T3. `test_i2c_scan_bus_shorted` (`test_monitor.c:707-720`), the four assertions on the truncated list - VERIFIED SURVIVING.**
Mutation: `MON_OK_PAYLOAD_MAX` from `MONITOR_LINE_MAX - 10` to `MONITOR_LINE_MAX - 18`. **237/237 still pass.** The test pins that the answer is `OK` not `ERR`, that it starts `08 09 0A`, that it fits the line, and that truncation lands on a token boundary, but never how many addresses survive, so any off-by-N in the payload budget passes. This is the assertion set for the one command SPEC 2.3 exempts from the reject-do-not-truncate rule, which makes its budget the thing most worth pinning. Fix: assert the exact response string, or at least the address count.

**T4. `test_bad_bytes` (`test_monitor.c:628-643`).**
Mutation not built: `process_line`'s `g_line[i] > 0x7F` -> `> 0x7E`. No test feeds a 0x7F or a 0x01-0x1F byte inside a command line, so the mutant passes while breaking the SPEC 2.1 clause "the firmware's command parser additionally tolerates low control bytes (0x01-0x1F and 0x7F) mid-line". The suite drives the rejection of 0x00 and 0x80 and never drives the tolerance beside them. Registry **class 29** ("the negative is never asserted"), inverted: here it is the *positive* of a documented tolerance that is never asserted. Fix: `expect_cmd("DEL tolerated mid-line", ">1 pi\x7Fng\n", "<1 ERR 1 badcmd\n")` plus a 0x01 case.

**T5. `test_tokenizer` (`test_monitor.c:194-199`), `expect_cmd("crlf tolerated", ">3 ping\r\n", ...)`.**
Mutation not built: in `assemble_one`, `if (c == '\r') continue;` -> only strip CR when it is the byte immediately before LF. The test feeds CR only in the terminator position, so it cannot tell the two behaviours apart, yet SPEC 5.4 states the stronger one ("a bare CR is discarded **anywhere** in the line, so a CRLF sender needs no configuration"). A stated invariant is a claim, not a mechanism. Fix: `expect_cmd("CR mid-line dropped", ">1 pi\rng\n", "<1 OK monitor 1 testmon\n")`, which I ran against the real module and it passes today.

Two further observations on the suite, neither a finding:

- The wrapper (`host/tests/test_firmware_monitor.py`) already handles class 30 properly: it parses the `<n>/<n> checks passed` summary rather than trusting make's exit code, and it runs the ASan leg as a separate case with a link probe so MinGW skips cleanly. It does not pin the total, so a mutation that deletes half the cases still reports "119/119 passed"; the same limit is documented for `test_webui_js.py`, so it is a known and accepted one.
- `test_can_dlc_clamp`, `test_can_partial_fill`, `test_unterminated_ok_payload`, `test_info_extra`, `test_i2c_short_read` and `test_uart_read_overreport` are the class 41 regression set from the 2026-08-28 round and they are good: each drives a shim that misbehaves in the specific way the contract permits. F1 is the one output parameter that set has no case for.

---

## The two questions

**1. Least confident claim, and how I rechecked it.**

F1's severity. My first draft called it HIGH on the strength of "printf `%s` with NULL is undefined behaviour", which is true of the standard and close to irrelevant on the two libcs anyone actually links. I rechecked it three ways. First I drove it: glibc prints `(null)` and the line is well-formed, so on the host suite and on any glibc-linked bench tool this never surfaces as anything. Second I checked whether the contract really leaves NULL open, since if `monitor.h` obliged non-NULL the finding would be a shim bug and not a monitor bug: `monitor.h:157-158` says only `state = current` and `INTEGRATION.md:282` names three literals without excluding NULL, so it is open. Third I looked for the precedent in the same file rather than reasoning from the standard, and found `cmd_ping` guarding exactly this shape on `p->name` one screen above. That precedent is what settled it at MED: the codebase has already decided that a port-layer pointer gets a NULL guard, and this is the site that did not get one. What I am still not certain of is the exact set of embedded libcs that fault rather than print `(null)`, and I have not tested any of them, so the report says "several" and names them as examples rather than asserting a specific toolchain fails.

**2. What should have been checked that nobody asked for.**

The brief scoped me to the C and to SPEC sections 2 and 5, which means the whole question of whether the three downstream copies are still identical to upstream went unasked. `MEMORY.md` records them as identical as of 2026-09-02 and records that the `!e` change was vendored to all three, but nothing in this repo can verify that, and every finding above is written as "requires re-vendoring" against copies whose current state I did not confirm. A drifted copy makes the re-vendoring note actively misleading: it reads as "apply this patch" when the real instruction may be "reconcile first, then apply". The check is cheap and mechanical (strip comments and diff each of the three against `firmware/monitor/`), it is the check `MEMORY.md` already prescribes before copying, and it should run before any of F1, F3 or F4 is applied rather than after.

The second thing nobody asked for: `make arm-check` is deliberately not wired into pytest, for a stated and good reason, which means SPEC 5.1's freestanding rules ("no HAL/LL/CMSIS, no floating point, C99, static buffers only") have no enforcement on any machine that lacks `arm-none-eabi-gcc`. Two of the constraints are greppable without a cross compiler and neither is grepped: no `#include` outside the six headers `INTEGRATION.md:22` lists, and no floating-point type in either `.c`. Both would be one-line pytest assertions that run everywhere, and the second one matters more than it looks, because `valid_plot_scale` parses a float grammar without a float type and the obvious "improvement" to it is to call `strtod`.
