# Review round 2, module-reading leg: sim.py / protocol.py / link.py / tools/mcu_sim.py

HEAD: `fd76735 POST /ports held to the config-write bar` (matches the expected sha).

Scope read end to end: `host/mcuscope/sim.py` (1049 lines), `host/mcuscope/protocol.py` (928), `host/mcuscope/link.py` (276), `tools/mcu_sim.py` (28).
Contract read: `docs/SPEC.md` sections 2.1-2.5, 5.4, 7, and `firmware/monitor/monitor.c` `tokenize()` / `recover_seq()` / `process_line()` as the reference firmware.

Probes ran from `/home/daniel/git/mcuscope/host` with `uv run python /tmp/probe*.py`. Nothing was written inside the repo.

Counts: 1 HIGH, 4 MED, 8 LOW.

## What held up

A 3 second conformance sweep of every line the simulator emits under `--plot --flood 50 --garbage`, plus command-driven output (480 lines: `!ps`, `!p`, `!can`, `!pd`, `!m`, `<` responses, flood/alive debug), round-tripped clean through `protocol.py`: zero oversized lines, zero non-printable characters, zero decode rejections.
The id-wrap echo is correct in both directions (`can tx 7FF` echoes id `0`, `can tx 1FFFFFFF x` echoes ext id `0`).
The 12-token cap, the 255-byte line assembly, the `ERR 8 overflow` recovery and the seq/no-command cases all match `monitor.c` case for case (`>`, `>7`, `>0`, `>65536 ping`, `>+7 ping`, `>7_0 ping` all agree with the firmware's behaviour).
The `!pd` name-uniqueness rule correctly rejects a channel that collides with one of its own bit lanes.
`link.py` reads clean: the lock covers both `feed` and `poll`, `BurstThenError` preserves the error across a partial read, and `validate_device` closes the `serial_for_url` gadget schemes.

---

## HIGH

### H1. `f4` decode accepts Inf/NaN, and each one breaks a different part of the stack

**CONFIRMED. HIGH.**
`host/mcuscope/protocol.py:707-718` (`_decode_field`), against `host/mcuscope/protocol.py:541-559` (`parse_plot_value`).

**Invariant broken.** The two plot decoders must agree on what a value is. `parse_plot_value` explicitly refuses a non-finite result, with the reason in its own comment: "Storing that would poison the channel's whole y range, so treat it as malformed like any other bad token." SPEC 2.5 says the same for the ad-hoc format: "A literal that overflows to infinity (`1e999`) is malformed." `_decode_field` on the typed path has no such check, so the identical value is malformed via `!p` and accepted via `!ps`.

```
!pd 0 volts:f4
!ps 0 A 7F800000    ->  ('volts', inf)
!ps 0 A FF800000    ->  ('volts', -inf)
!ps 0 A 7FC00000    ->  ('volts', nan)
!p 1 volts=1e999    ->  None  (rejected)
```

**Failure scenario 1, NaN, storage.** sqlite3 binds a Python NaN as NULL, so the `plot_points.value NOT NULL` constraint fires from inside the line-store path:

```
File "host/mcuscope/store.py", line 794, in _insert_children
sqlite3.IntegrityError: NOT NULL constraint failed: plot_points.value
```

A grammar-legal wire line therefore raises out of the store's insert, not out of a decoder that is documented to answer `None` for exactly this case.

**Failure scenario 2, Inf, REST.** Inf stores fine as REAL and comes back out of `query_plot_series`, and FastAPI's `JSONResponse.render` uses `allow_nan=False`:

```
rows: [{'line_id': 2, 'ts': 1.0, 'tick_ms': 10, 'value': inf}, {'line_id': 3, ..., 'value': 10.0}]
GET /plot/series RENDER FAILS: ValueError Out of range float values are not JSON compliant: inf
```

So `GET /plot/series` (`host/mcuscope/server.py:1431`) returns 500 for that channel's whole window until the point ages out of retention, and `/plot/export` and any WS push carrying the point go the same way. Note `query_plot_channels` still answers, so the channel looks healthy while its series is unfetchable.

**Reachability.** `0x7F800000` / `0x7FC00000` is what an uninitialised float, a `0.0/0.0`, or an overflowing accumulator holds in real firmware, and SPEC 2.5 makes `f4` "transmitted as its raw 32-bit pattern" precisely so firmware does no formatting and no validation of it. This needs no hostile target.

**Read of intent.** The SPEC side is settled: non-finite is malformed on the `!p` path, and there is no reason the typed path should differ. `_decode_field` should answer `None` on a non-finite `f4`, which routes the line to a generic event row exactly as a width mismatch already does.

---

## MED

### M1. `serve_pty` wedges permanently in a blocking write with no reader on the slave

**CONFIRMED. MED.** `host/mcuscope/sim.py:913-915` (`write_lines`), against `host/mcuscope/sim.py:766-800` (`_sock_send_lines`).

**Invariant broken.** The TCP path has an explicit budget for this: `SEND_STALL_TIMEOUT_S = 5.0`, resumed from the unsent offset, with the docstring naming the failure it prevents. The pty path calls a single blocking `os.write(master, ...)` with no timeout, no partial-write handling and no stall detection.

**Failure scenario.** Start `mcu-sim --pty --plot` and do not attach anything. The slave's input queue (4 kB) fills with the sim's own output and the write blocks forever. Sampled the serving thread's stack every 5 s:

```
t=  5s no reader -> sim.py:920 serve_pty        (select)
t= 10s no reader -> sim.py:915 write_lines      (blocked in os.write)
t= 15s ... t= 60s -> sim.py:915 write_lines     (still blocked)
```

While blocked it reads nothing, polls nothing and cannot see the `KeyboardInterrupt` path, yet the slave path is printed and stat-able, so a daemon's presence check succeeds and it attaches to a corpse. This is the same "healthy while dead" shape `serve_listener` was hardened against.

**Mitigating.** It recovers once a reader opens the slave (confirmed: attaching a pyserial handle returns the thread to `select`), and at the default rates (no `--plot`) it survived 30 s. So the window is "sim started before the daemon", which is exactly the documented `mcu-sim --pty` workflow, plus any reader that pauses draining.

### M2. `--pty` never puts the pty in raw mode, so the line discipline mangles and echoes the sim's output

**CONFIRMED. MED.** `host/mcuscope/sim.py:904` (`pty.openpty()` with no `termios` call).

`pty.openpty()` leaves the slave in canonical mode with `ECHO`, `ICANON`, `OPOST`/`ONLCR` all on. Measured on the raw pair:

```
slave lflag ECHO: True  ICANON: True
slave oflag OPOST: True  ONLCR: True

sim emitted        : b'\x01\x02\x7f binary junk \x00 line\nsim alive n=1\n'
daemon sees at slave: b'\x01 binary junk \x00 line\n'
echoed back into the sim's own read path: 47 bytes  b'^A^B\x08 \x08\x08 \x08 binary junk ^@ line\r\nsim alive n=1\r\n'
```

Three separate consequences:

- **Byte-level corruption of sim output.** `\x7f` is `VERASE`, so the line discipline deleted both it and the preceding `\x02` before the host ever saw them. Any `\x7f`, `\x08`, `\x15` or `\x17` reaching an event payload is silently rewritten. This is what `--garbage` emits by design, and SPEC 7 lists `--garbage` as a supported fault injector on either transport, so the two transports do not inject the same fault.
- **Self-echo loop.** Everything the sim writes to the master comes straight back as readable input on the master, control characters expanded to `^A^B` and erase sequences. `select([master])` is therefore always readable, the loop never idles, and `_process_incoming` re-parses the sim's whole output stream. It also doubles the pressure feeding M1.
- **`\n` becomes `\r\n` on the slave's output** via `ONLCR`, which the CR-stripping in `_process_incoming` happens to absorb.

**Mitigating.** Once pyserial opens the slave it calls `cfmakeraw`, clearing all three, so a promptly attached daemon never sees this. It is the pre-attach window and any non-pyserial reader (`cat /dev/pts/N`) that is affected. `tests/test_sim_pty.py` is one test and attaches immediately, so nothing covers the window.

### M3. Host tokenizer splits on any whitespace; the reference firmware splits on `' '` only

**CONFIRMED. MED.** `host/mcuscope/protocol.py:226` (`line[1:].split()`), against `firmware/monitor/monitor.c:690-712` (`tokenize`, `g_line[i] == ' '`) and `monitor.c:751` (rejects `'\0'` and `> 0x7F` but not other control bytes).

SPEC 2.1: "Encoding: 7-bit printable ASCII... Tokens are separated by single spaces."

```
>1 gpio\tset\tled\t1   sim -> ['<1 OK']   (and it really drove the GPIO)
>1 ping\x0bx           sim -> ['<1 OK monitor 1 sim']
```

The reference firmware sees one token `gpio\tset\tled\t1` and answers `ERR 1 badcmd`. So the simulator, whose stated job is to be a conformant firmware and executable documentation of the protocol, accepts and acts on lines the SPEC forbids and the reference implementation refuses. `_is_gpio_set` inherits it and fires the SPEC 7 debug burst for the same line.

The host send path does not stop it either: `serial_link._encode_wire` rejects only `\n`, `\r` and non-ASCII, so `mcu send $'>1 gpio\tset\tled\t1'` reaches the wire.

**Read of intent.** SPEC 2.1 is the intended side. Either `parse_command` should split on `' '` only (matching `tokenize`), or the printable-ASCII rule should be enforced on the line before tokenizing. The former is the smaller change and makes the two engines byte-identical.

### M4. The simulator reflects unsanitized control bytes into its response payloads

**CONFIRMED. MED.** `host/mcuscope/sim.py:202` (`f"unknown {name}"`), `:325` (`f"unknown cs {cs}"`), `:351` (`f"unknown adc {rest[0]}"`), and `host/mcuscope/sim.py:731-756` (`encode_lines`).

SPEC 2.2: "A firmware also sanitizes each outgoing line: any byte outside printable ASCII is replaced before the line is pushed."

```
>1 pi\x00ng   ->  ['<1 ERR 1 badcmd unknown pi\x00ng']
```

`encode_lines` is the one place every outgoing line passes through and it does the ASCII coercion and the length enforcement, but not the printable-ASCII replacement, so an attacker-chosen control byte (NUL, an ANSI escape introducer, a backspace) is echoed back verbatim into the terminal view, the exports and the web UI. `monitor.c:751` rejects such a line whole with `ERR 2 badarg` and never reflects it.

`encode_lines` is the right place for the sanitization, and it is where the docstring already claims the outgoing line is held "within SPEC 2.1's limits".

---

## LOW

### L1. `encode_lines([])` emits a spurious blank line

**CONFIRMED. LOW.** `host/mcuscope/sim.py:756`. `("\n".join([]) + "\n")` gives `b'\n'`. Every caller today guards with `if lines:` (`_sock_send_lines`, `SimSource.feed/poll`, `write_lines`), so it is latent, but the function's contract is "encode a pass's output" and an empty pass should encode to `b""`.

### L2. Protocol emitters accept an embedded LF and forge a second wire line

**CONFIRMED. LOW.** `host/mcuscope/protocol.py:248` / `:261` / `:212` / `:880`.

```
format_command(1, "mark a\n>2 gpio set led 1") -> '>1 mark a\n>2 gpio set led 1'
format_response_ok(1, "AA\n<1 OK BB")          -> '<1 OK AA\n<1 OK BB'
format_marker("hi\n!m forged", None)           -> '!m hi\n!m forged'
```

`format_marker` already rejects a text whose first word is a tick sigil for exactly this "silent corruption rather than a failure" reason, so the newline case is the same class left open. The daemon's outbound path is covered (`serial_link._encode_wire` refuses `\n`/`\r`), and nothing on the simulator's side is: `encode_lines` passes an embedded LF straight through (`S.encode_lines(["<1 OK A\n<1 OK B"])` gives two wire lines).

### L3. `format_can_event` silently drops the payload of an RTR frame that carries one

**CONFIRMED. LOW.** `host/mcuscope/protocol.py:368-372`, with `CanFrame.__post_init__` at `:322-324` deliberately not normalising `dlc` for RTR.

```
CanFrame(can_id=0x100, data=b"\x01\x02", rtr=True, dlc=3, tick_ms=1)
  encode: !can 1 r 100 3
  decode: CanFrame(can_id=256, data=b'', rtr=True, dlc=3, tick_ms=1)
```

Every other field in this function raises on an inconsistency (id out of range, tick out of range, dlc out of range, payload too long); this one loses data quietly. Not live (the only caller builds RTR frames from `parse_can_tx_args`, which never sets both).

### L4. `normalize_line` strips more than its docstring claims

**CONFIRMED. LOW.** `host/mcuscope/protocol.py:68-70`. The docstring says "Strip a single trailing CRLF/LF/CR pair"; `rstrip("\r\n")` strips every trailing CR and LF (`normalize_line("a\n\n\n\r\r") == "a"`). Harmless today because line assembly upstream already splits on LF, but the stated invariant is not the implemented one.

### L5. `_can_passes_filter` never consults the `x` flag it stored

**CONFIRMED. LOW.** `host/mcuscope/sim.py:257-263` versus `:253` (`st.can_filter_ext = len(rest) == 3`).

```
>1 can filter 100 7FF x   -> ['<1 OK']
_can_passes_filter(0x100) -> True     (a standard-id frame passing an extended-only filter)
```

`can_filter_ext` is set and never read. SPEC 2.4 says `x` "is passed to the port layer" and leaves the effect to it, so this is not a hard contract breach, but in the simulator the filter *is* the port layer, so the flag has nowhere else to take effect and is simply inert.

### L6. Host `parse_command` accepts leading whitespace that neither `_recover_seq` nor the firmware does

**CONFIRMED. LOW.** `host/mcuscope/protocol.py:226` versus `host/mcuscope/sim.py:562` and `monitor.c:717-727`.

```
parse_command(">  7 ping") -> Command(seq=7, tokens=('ping',))
_recover_seq(">  7 ping")  -> None
```

`monitor.c` has the identical split (its `tokenize` skips leading spaces, its `recover_seq` does not), so the simulator is faithful to the firmware here and the host decoder is the looser of the three. Worth noting only because it is one more place `str.split()` is doing work the wire grammar does not allow (see M3).

### L7. No 12-token cap on the host's outbound path

**SUSPECTED. LOW.** `host/mcuscope/protocol.py:212-218` (`format_command`) and `host/mcuscope/serial_link.py:917-931` (`_encode_wire`).

SPEC 2.3 pins the cap at 12 tokens including the seq and both reference implementations enforce it on receive, but nothing enforces it on send:

```
p.format_command(1, "a "*20) -> a 21-token line, accepted
sim answer                   -> ['<1 ERR 2 badarg too many tokens']
```

The round trip is correct (a conformant target refuses it) and it is only length that `_encode_wire` checks, so this costs a wasted round trip rather than a wrong result. Filed because `_encode_wire` already exists to catch exactly this class locally.

### L8. Out-of-range `--tcp-port` crashes with a traceback

**CONFIRMED. LOW.** `host/mcuscope/sim.py:986-992`, `:638`.

```
mcu-sim --tcp-port 70000  ->  UNCAUGHT OverflowError: bind(): port must be 0-65535.
```

`console_entry`'s crash-file backstop catches it, so the user gets a crash report for a typo rather than a usage error.

---

## Checked and clean, recorded so the next round does not re-derive it

- Both `--flood` and the periodic signals are burst-bounded, and `_due_beats` cannot return a `next_due` in the past. The deliberate difference (periodic signals re-anchor and drop the backlog, `--flood` backfills at up to 5000 per pass) matches SPEC 7 as written.
- `PlotDecoder._defs` is bounded at 10 entries by the single-digit sid rule; `pending_echoes` and `async_lines` drain every poll; the `rx` assembly buffer is capped at `MAX_LINE_BYTES` and a 10000-byte no-LF write leaves it at exactly 255. No unbounded growth found under hostile input.
- `parse_hex_int` / `is_decimal_token` / `parse_seq_token` / `_TICK_HEX_RE` all gate CPython's `int()` digit limit correctly and none of the decode paths can raise a bare `ValueError` out to a caller expecting `None`.
- Oversized responses become `ERR 8 overflow`, oversized events truncate. The one SPEC 2.3 exception (`i2c scan` truncating on a whole token) is unreachable in the sim, which scans exactly two addresses.
- `can filter <id> <mask> r` is refused with `badarg` and a third token other than `x` is refused, per SPEC 2.4. Unbounded filter id/mask is explicitly permitted by SPEC 2.4 for the simulator.
- `validate_device` closes `spy://`, `loop://`, `hwgrep://`, `alt://` and any `?query=` form; scheme matching is case-insensitive and a leading-space device fails closed.
- `tools/mcu_sim.py` is a correct shim; `import *` re-exports the module alias `p` alongside the public API, which is untidy but harmless.
