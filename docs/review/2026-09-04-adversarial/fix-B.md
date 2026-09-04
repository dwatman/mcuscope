# Fix batch B (2026-09-04 adversarial round)

Base: `7a1120f`. Not committed.

## What changed

- **B1 - a break over `socket://` is refused, not swallowed** (`host/mcuscope/link.py`).
  `SerialLink.send_break` returns False when `_socket_drain` is set: pyserial's socket handler inherits `send_break` from `SerialBase` but its `_update_break_state` only logs, so the break never left the host and `/break` answered 200.
  The `hasattr` check stays after it for third-party handlers; `rfc2217://` is untouched and still sends `SET_CONTROL_BREAK`.
- **B2 - `/send` no longer strips a trailing terminator** (`host/mcuscope/serial_link.py`).
  `send_raw` passes `line` to `_encode_wire` unchanged, so a body containing CR or LF is refused as SPEC 3.4 requires and as `send_command` already did. Before, `{"line": "\r"}` wrote zero bytes and answered ok.
- **B3 - `disconnect_reason` starts at `connecting`** (`host/mcuscope/serial_link.py`, `docs/SPEC.md`).
  null is reserved for a connected port, and the `POST /ports` response is built before the first open attempt resolves, so an agent read `connected=false` with reason null. `_on_connect` still clears it to None; `hold()` and `_on_error` still overwrite it. SPEC section 3.4: the enum line gained `"connecting"` and the sentence below it says a port whose first open attempt has not completed reports it.
- **B4 - a break is observable in the harness** (`host/mcuscope/link.py`, `host/tests/support.py`).
  `SourceLink` takes an optional `on_break` callable; `SimEndpoint` gains `breaks: list[float]`, appended from `_Unpluggable.on_break`, the same shim path `written` uses.
- **B5 - two guard tests, no code change** (`host/tests/test_port_health.py`): `_encode_wire("x", "cr")` raises PortError, and `status()` masks the stored reason only while connected.
- **B6 - a dying store writer no longer strands futures** (`host/mcuscope/store.py`).
  Two strands, one class:
  - the writer task gets a done-callback (`_writer_exited`) that calls `_fail_queued("store writer exited")` when it ended with an exception. A cancelled task and a clean sentinel exit return early, so `stop()` keeps sole ownership of the queue on its own path.
  - the batch already taken off the queue is unreachable from that callback, so the per-batch `try` gained an `except Exception` that fails the batch's unresolved futures and re-raises. Without it the queue-level fix leaves up to `_MAX_BATCH_ROWS` awaiters hanging, which is the same defect one step earlier.

## B2 caller sweep (asked for before the change)

Four call sites of `POST /send` / `send_raw`, all accounted for; none relies on the strip.

| Site | Verdict |
| --- | --- |
| `host/mcuscope/cli.py:400` (`mcu send TEXT`) | complies. A shell argument carries no trailing newline; one written as `$'x\r'` now gets a 400 instead of a silently different write. |
| `host/mcuscope/cli.py:443` (`mcu sysrq`) | complies. Exactly one character, and CR/LF as the SysRq key is meaningless; it was already refused as two characters. |
| `host/mcuscope/webui/cmdbar.js:110` (raw mode) | complies. The text is `input.value.trim()`, so a terminator cannot survive to the request. |
| `host/mcuscope/server.py:1924, 1988, 2241` (`/wait --repeat-ms`, `/wait send`, `/assert send`) | complies. Same request body, validated the same way; nothing appends a terminator before the call. |

## Revert verification

Each fix reverted by hand, its test run, then re-applied. Every case failed while reverted.

| Item | Test | Fails when reverted |
| --- | --- | --- |
| B1 | `test_link.py::test_send_break_over_socket_is_refused_not_swallowed`, `test_break.py::test_break_over_socket_is_refused` | yes (2 failed) |
| B2 | `test_port_health.py::test_send_refuses_a_terminator_in_the_body` (4 params) | yes (4 failed) |
| B3 | `test_port_health.py::test_a_port_reports_connecting_before_its_first_open_attempt` | yes |
| B4 hook | `test_break.py::test_break_reaches_the_transport`, `test_link.py::test_source_link_reports_the_break_to_its_hook` | yes (2 failed) |
| B4 closed link | `test_link.py::test_source_link_break_on_a_closed_link_raises` | yes |
| B5 eol guard (mutation: unknown eol defaults to LF) | `test_port_health.py::test_encode_wire_refuses_an_unknown_line_ending` | yes |
| B5 status mask (mutation: report the stored reason unconditionally) | `test_port_health.py::test_status_masks_the_reason_only_while_connected` | yes |
| B6 done-callback | `test_store_writer.py::test_a_writer_that_dies_fails_what_is_still_queued` | yes (hangs to the 2 s `wait_for`) |
| B6 in-flight batch | `test_store_writer.py::test_a_writer_that_dies_mid_batch_fails_that_batch` | yes (hangs to the 2 s `wait_for`) |

B5 is a guard over code that was already correct, so "reverted" there means a mutation of the behaviour under test; both mutations were caught.

## Gates

- `uv run python -m ruff check .` clean.
- `pytest tests/test_break.py tests/test_port_health.py tests/test_eol.py tests/test_link.py tests/test_store_writer.py tests/test_sim_tcp.py tests/test_e2e.py -q`: 157 passed, 1 failed.
  The failure is `test_e2e.py::test_ws_port_filter`, from another agent's in-progress change to `server.py` (a new `websocket.close(code=1008)` for an unattached alias, which the test's `recv` on `?port=ZZZ_nope` does not expect). Nothing in batch B touches that path; `test_e2e.py` and `server.py` are outside this batch's file list.

## Deviations

- The B6 test asked for "submit two lines, both resolve with StoreError". Two futures do, but a third line is needed: the first row of a batch is resolved with its row before the broadcast raises, so it can never carry a StoreError. Both tests submit three lines, and the second test asserts the first future resolves with its row.
- The B1(b) socket-level test lives in `tests/test_break.py`, not `tests/test_sim_tcp.py` (outside this batch's file list); it spawns the same real listener that file uses.
- The B2 endpoint tests live in `tests/test_port_health.py` next to the `_encode_wire` guard, for the same reason: `tests/test_eol.py` was not editable in this batch.
