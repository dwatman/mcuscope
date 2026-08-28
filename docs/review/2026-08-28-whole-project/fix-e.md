# Fix batch E: sim.py / protocol.py (+ their tests)

Base: `fd76735`. Files touched: `host/mcuscope/protocol.py`, `host/mcuscope/sim.py`,
`host/tests/test_protocol.py`, `host/tests/test_sim.py`, `host/tests/test_sim_pty.py`,
new `host/tests/test_review_r2_sim.py`. Nothing committed. No docs touched.

## Items

### SP-H1 + RG-F12/F13 (HIGH) non-finite plot values

- `_decode_field`: an `f4` whose bit pattern decodes non-finite answers `None`, so the line
  becomes a generic event exactly as a width mismatch does (`plots.js:195` is the mirror).
- `decode_plot_sample`: finiteness re-checked after `*scale`, which is the integer-channel
  case `_decode_field` cannot see (`plots.js:227` is the mirror).
- Tests: `tests/test_review_r2_sim.py` drives all three bit patterns (7F800000, FF800000,
  7FC00000) and a `u4*1e308` post-scale overflow through a live daemon: no plot channel is
  created, `GET /plot/series` answers 200 with no points for both channels of the line, and
  the raw line is still stored. Unit halves in `test_protocol.py`.

### SP-M1 (MED) serve_pty wedge

- The pty write loop is now module-level `_pty_write_lines(master, lines, budget)`: nonblocking
  master, unsent-offset resume, `SEND_STALL_TIMEOUT_S` budget, and past the budget it drops the
  backlog and returns False instead of ending the session (a pty slave with nothing attached is
  the documented `mcu-sim --pty` startup window, unlike a socket peer that stopped reading).
- `serve_pty` sets `os.set_blocking(master, False)`; the read side treats `BlockingIOError` as
  "nothing to read", distinct from EOF.
- Test is at the write loop, not through a subprocess, on purpose: **a subprocess round trip
  cannot distinguish the wedge**. Attaching a reader releases the blocked `os.write`, so the
  "no reader, heavy output, then attach and ping" test the ruling describes passes with the
  fix reverted (verified: it did). The unit test runs the write in a thread with a join
  timeout, so the wedge fails as "thread still alive" rather than hanging the suite, and also
  asserts the recovery leg (after a reader drains, the next pass goes out in full).

### SP-M2 (MED) pty raw mode

`tty.setraw(slave)` at openpty. Test opens the slave with plain `os.open` (pyserial would set
raw itself) and asserts ECHO, ICANON, OPOST and ICRNL are all clear.

### SP-M3 + SP-L6 (MED) tokenizer parity

Read `monitor.c` `tokenize()` and `recover_seq()` first. New `protocol.split_tokens(body)` is
`[t for t in body.split(" ") if t]`, which is byte-for-byte what `tokenize()` does: space runs
collapse, leading spaces are skipped, a tab or vertical tab is an ordinary token byte. Used by
`parse_command` and by the sim's no-command recovery path. The L6 asymmetry is preserved
deliberately: `tokenize()` skips leading spaces (so `>  7 ping` parses) while `recover_seq()`
does not, and `sim._recover_seq` is unchanged. Tests assert the tab and VT probes answer
`ERR 1 badcmd`, the GPIO was not driven, and no SPEC 7 debug burst fires.

### SP-M4 + SD2 (MED) outgoing sanitization

`encode_lines` replaces every byte outside 0x20..0x7E with `.` before the length rules, one
site for every outgoing line (`monitor.c:213` write_line). Tests: `>1 pi\x00ng` reflects as
`<1 ERR 1 badcmd unknown pi.ng`, and a `mark` carrying 0x01 is sanitized on the `!m` event.

### SP-L1..L5, L8

- L1 `encode_lines([]) == b""`.
- L2 `_check_no_break` refusal on CR/LF in `format_command`, `format_response_ok`,
  `format_response_err` and `format_marker`. No live caller hits it (see "suites run").
- L3 `format_can_event` raises on an RTR frame carrying a payload.
- L4 `normalize_line` strips exactly one CRLF/LF/CR, as its docstring says.
- L5 `_can_passes_filter(can_id, ext)` honours `can_filter_ext`; all three call sites pass the
  frame's ext flag. Test: `can filter 0 0 x` passes only the extended id on the CAN_BUS.
- L8 `--tcp-port` goes through `_tcp_port_arg`, so out of range / non-numeric is an argparse
  usage error (exit 2, "must be 0..65535") instead of an OverflowError crash file. 0 stays
  legal, since it is the documented ephemeral-port spelling.

### SP-L7 (contradiction to report)

The cap is in `format_command` (raises past 12 tokens including the seq, counted with
`split_tokens` so space runs are one separator). `MAX_COMMAND_TOKENS` moved to protocol.py;
sim.py aliases it.

**It does not land on /send.** `format_command` has exactly one caller,
`serial_link.send_command` (the `/cmd` path). `/send` goes `send_raw` -> `_encode_wire`
directly and never builds a command line, so a 20-token raw `>1 a a a ...` still reaches the
wire and is refused by the target. Closing /send needs a line in `serial_link._encode_wire`,
which belongs to another batch.

## Consequence to route to the docs batch

`--garbage` no longer emits non-printable bytes: its junk line goes through `encode_lines`
like everything else and comes out `.... binary junk . line`. That is what real firmware does
(monitor.c rejects such a line whole), and it is what the SD2/SP-M4 ruling asks for, but
SPEC 7 still calls `--garbage` "occasionally emit binary junk". Wording should follow.
No test asserted the sim's junk bytes; `test_e2e.py::test_garbage_line_ingested` drives the
`/send` path and is unaffected.

## Revert-verification (each behaviour fix, fix reverted -> its test fails)

| item | revert | result |
|---|---|---|
| SP-H1 (f4) | drop the isfinite gate in `_decode_field` | `test_review_r2_sim` fails: `sqlite3.IntegrityError: NOT NULL constraint failed: plot_points.value` |
| RG-F13 (post-scale) | drop the post-scale gate | `test_post_scale_overflow_is_a_generic_event` fails |
| SP-M1 | restore the single blocking `os.write` | wedge test fails: "the write wedged with no reader on the slave" (thread alive after 10 s) |
| SP-M2 | drop `tty.setraw` | raw test fails on ECHO |
| SP-M3 | `body.split()` | 3 tests fail (split_tokens, parse_command tabs, sim tab verdict) |
| SP-M4 | drop `_sanitize` | control-byte test fails |
| SP-L1 | drop the empty guard | empty-pass test fails |
| SP-L2 | make `_check_no_break` a no-op | emitter refusal test fails |
| SP-L3 | drop the RTR check | RTR test fails |
| SP-L4 | restore `rstrip("\r\n")` | normalize test fails |
| SP-L5 | drop the ext gate | filter-x test fails |
| SP-L7 | disable the cap | token-cap test fails |
| SP-L8 | `type=int` | usage-error test fails |

## Suites run (from host/, uv run python -m pytest)

- `test_sim.py test_sim_tcp.py test_sim_pty.py test_protocol.py test_source_link.py
  test_review_r2_sim.py`: 271 passed.
- Blast-radius smoke for the protocol changes: `test_plot.py test_e2e.py test_regressions.py
  test_webui.py`: 146 passed. Confirms SP-L2 has no live caller.
- `ruff check` clean on all six touched files.
