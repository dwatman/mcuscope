# Serial link leg (orchestrator, HEAD 7a1120f)

Scope: `serial_link.py`, `link.py`, the `/wait` repeat and `/break` handlers in `server.py`, `daemon.py` attach wiring.
Method: full read of the reader thread, loop bridge, stop/hold/attach ordering and the three new paths (break, eol, repeat); each suspicion driven against pyserial source or the in-process stack.

## Findings

### L1 HIGH: a break over `socket://` reports success and files a sys row while nothing was sent
- `link.py:177` `SerialLink.send_break` gates on `hasattr(self._ser, "send_break")`. `serial.SerialBase` defines `send_break`, so the gate never fails for any pyserial handle.
- pyserial's socket handler (`urlhandler/protocol_socket.py`) implements `_update_break_state` as a logger call and nothing else; `send_break` sets the flag, sleeps `duration`, clears it. Verified by reading the installed source.
- Result: `POST /break` on a `socket://` port answers `{"ok": true}` after sleeping `ms`, and `port <alias>: break N ms` is stored. SPEC 3.4 says this transport is a 400 ("a transport that cannot send a break (a `socket://` link) is a 400 too"). `mcu sysrq` on such a port then also "succeeds".
- Class 17 (reported value is the request, not the result), with the class 27 shape underneath: the test double (`SourceLink.send_break` returns True) is gentler than the real handler, and no test opens a break over a real `socket://` link.
- `rfc2217://` is real (its `_update_break_state` sends `SET_CONTROL_BREAK_ON/OFF`); native ports are real. Only the socket handler lies.
- Fix: in `SerialLink.send_break`, return False when `self._socket_drain` is set (the same flag `drain` already keys on), before the `hasattr` check. Keep the `hasattr` check for third-party handlers.
- Test (fails without the fix): in `tests/test_sim_tcp.py` (the tier that has a real TCP listener), attach `socket://127.0.0.1:<port>` and `POST /break` -> 400 with `"cannot send a break"` in the body, and no `sys` row matching `break` afterwards. Plus a unit test in `tests/test_link.py` or nearest: `SerialLink(ser, "socket://127.0.0.1:1").send_break(0.01) is False` with `ser` a `serial.serial_for_url("socket://...", do_not_open=True)` stand-in that has `send_break`.

### L2 MED: `repeat_ms` turns a rejected line body into a full-timeout retry loop
- `server.py:1924` `_repeat_send` catches every `PortError` from `send_raw` as a counted failure. `send_raw` raises `PortError` for the body itself (embedded newline, non-ASCII, over 255 bytes) through `_encode_wire`, the same errors the non-repeat path returns as 400.
- Driven: `POST /wait {match: NEVER, timeout_ms: 500, send: "héllo", send_mode: raw, repeat_ms: 50}` -> 200 `{"status": "timeout", "sends": 0, "send_failures": 10}` after 0.5 s; the same body without `repeat_ms` -> 400 `line must be 7-bit ASCII`. Same for a 300-byte body.
- Class 17 for an agent: the answer says "nothing matched" when the truth is "your send was never sendable". A bootloader race is exactly where a wrong-looking timeout is expensive to diagnose.
- Fix: validate the body once before the repeater starts. In `_do_wait`, inside the `repeat_ms is not None` branch and before `create_task`: `try: port_obj.check_wire(body.send, body.eol) except PortError as exc: return _bad_request(str(exc))`, where `SerialPort.check_wire(line, eol)` is a two-line method: `self._encode_wire(line.rstrip("\r\n"), eol or self.eol)` (discard the result). Port-state errors (`not connected`, write failed) stay counted, as designed.
- Test (fails without the fix): in `tests/test_wait_repeat.py`, the three bodies (`"a\nb"`, `"héllo"`, `"x" * 300`) each answer 400 with the same message the non-repeat path gives, in under 100 ms.

### L3 LOW: a body of only CR under `eol: none` writes nothing and answers ok
- `serial_link.py:1085` `send_raw` strips trailing `\r\n` before `_encode_wire`, so `{"line": "\r", "eol": "none"}` writes zero bytes, counts `lines_tx`, stores an empty `cmd` row and returns `{"ok": true}`. SPEC 3.4 says the body may not contain CR or LF; the strip makes a trailing one accepted-and-dropped rather than refused, and only `none` makes the difference observable (with `lf`/`crlf` the terminator is re-added).
- `send_command` does not strip, so the two paths disagree on the same input.
- Fix: drop the `rstrip` in `send_raw` and let `_encode_wire` refuse, which matches SPEC and `send_command`. If the CLI relies on the strip for pasted lines, move a single `rstrip("\r\n")` into the CLI's `send`/`wait --raw` argument handling instead. Check `grep -n rstrip mcuscope/cli.py` first.
- Test: `POST /send {"line": "x\r", "eol": "none"}` -> 400 `embedded newlines`.

## Ruled out (read and reasoned, no finding)
- stop/hold/attach ordering: `hold()` sets `held` and `manual` before `stop()`; `_on_error` skips the reason while held; `_on_disconnect` after stop is withheld from sys rows by `_spawn_sys`'s stop gate; a second `stop()` from a later `reconnect` is idempotent (cancelled consumer awaited under suppress, `_fail_pending` empty).
- `_write_lock` holders: `_write_bytes`, `_break_locked` (both `to_thread`), the reader's finally, `_close_link_locked` (`_join_pool`). Every hold is bounded by `WRITE_TIMEOUT` or the break's 2 s cap; nothing takes it on the loop thread.
- Class 39 on the repeater: cancelled and awaited in `finally`, including on handler cancellation. Class 36: re-anchored, no backfill.
- `_repeat_send` looks the port up by alias per tick, so a `reconnect` mid-wait is followed. A held port counts failures at up to 100 Hz, each a cheap `to_thread` that raises before any I/O.
- Class 40: `disconnect_reason` is written only from loop callbacks; `_write_health` is one frozen object per store on both threads (the lost update between a worker failure and `_on_disconnect`'s reset is one count on a port that just dropped, and the reset wins, which is the right value).
- `_fail_pending` racing `send_command`'s write: the future is done before `wait_for`, `_discard_pending_future` retrieves the exception, the handler answers 400.
- Break during a link drop: the reader's finally waits for the lock (bounded 2 s), closes, posts `_on_disconnect`; the break's `send_break` raises `SerialException` on the closed handle and maps to 400.

## The two questions
1. Least confident: that the socket handler ignores the break rather than raising. Rechecked by printing `protocol_socket.Serial._update_break_state` from the installed pyserial (a logger call only) and `SerialBase.send_break` (flag, sleep, flag). Not rechecked on Windows, where the native path is `SetCommBreak`; no reason to expect a difference, but unmeasured.
2. The gap: every break test runs through `SourceLink`, whose `send_break` returns True so the sim can exercise the success path, which means the suite cannot see a transport lying. The same shape exists for `cancel_read`/`cancel_write` on socket links (correctly False) and is worth a one-line assertion each in the TCP tier. Also worth asking: what other `SerialBase` methods does the code gate with `hasattr`? `grep -n hasattr mcuscope/link.py` lists three, all against methods `SerialBase` defines; the two cancel methods are genuinely absent on the base (they live in `serialposix`/`serialwin32`), the break one is not.
