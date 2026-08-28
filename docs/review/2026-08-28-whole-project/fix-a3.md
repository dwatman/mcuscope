# Fix batch A3 (serial_link.py) - report

HEAD at start: `0b5eed9` (verified).
Files touched: `host/mcuscope/serial_link.py`, new `host/tests/test_review_r2_serial.py`, one line in `docs/SPEC.md` (3.4 `/status` port object). Not committed.

## Items

### 1. CD2 - detach handle-close off the default executor
`stop()`'s post-join close now runs `self._loop.run_in_executor(_join_pool, self._close_link_locked, link)`, per the module comment's own invariant. Comment added naming why (shared with session exports and stalled writes; this close is what frees an exclusive handle for the next attach).

Test `test_detach_handle_close_does_not_queue_behind_the_default_executor`: same starvation shape as the existing join test (default pool reduced to one occupied worker), plus a `_Wedged` Link whose `read` blocks and whose `cancel_read` answers False, so the reader outlives `JOIN_TIMEOUT` and `stop()` actually takes the close branch. Asserts `stop()` completes inside `JOIN_TIMEOUT + 5 s` and the handle is closed.

### 2. CD6 - attach pre-checks before prime
`PortManager.attach` runs two cheap checks before `prime_plot_defs`: the `MAX_PORTS` count (only when not replacing) and `self._closed or not self._store.writer_alive`. Both stay re-checked authoritatively under the lock. `writer_alive` is the public liveness property (False before start and after stop), so no private store attribute is touched.

Test `test_attach_against_a_stopped_store_is_a_port_error`: store started then stopped, `attach` raises `PortError` matching "detached" and no port is registered. Reverted, `prime_plot_defs -> store.max_id()` raises AssertionError.

### 3. RG-F4 - resolved device on the health surface
New `SerialPort.resolved_device` (None until connected), set in `_on_connect(dev)`, which receives exactly the string `_resolve_device()` handed to the open. `self.device` is untouched, so every reconnect re-resolves the serial number. `status()["device"]` is now `self.device or self.resolved_device or self.serial_number`.

SPEC 3.4, one line added above the `rx_dropped` paragraph: a port attached by `serial_number` reports the resolved device once connected, and the serial number until then.

Test `test_serial_number_port_reports_the_device_it_opened`: driven through the reader (not a hand-set attribute), monkeypatching `serial_link.cached_comports` to a fake `SN1 -> /dev/ttyFAKE7` and supplying an idle scripted `SourceLink` as the opener. Asserts "SN1" before connect, "/dev/ttyFAKE7" after, and `port.device is None`.

### 4. Class 39 - `_store_sys` orphaning a StoreError
`_store_sys` catches `StoreError` and logs it at debug ("sys row dropped"). `StoreError` imported from `.store`.

Test `test_sys_row_on_a_stopped_store_is_not_an_orphaned_task`: loop exception handler captured, store stopped, `_spawn_sys` fired, and the task is deliberately never awaited or gathered (an earlier draft used `asyncio.gather(..., return_exceptions=True)`, which retrieves the exception and made the test pass against the mutant). It waits for `_bg_tasks` to empty via the done-callback, `gc.collect()`, then asserts nothing reached the handler.

### 5. SP-L7 (routed from batch E) - 12-token cap on the raw outbound path
`_encode_wire` counts `p.split_tokens(body)` against `p.MAX_COMMAND_TOKENS` next to the existing length check, message `line has {n} tokens, over the 12 cap` (distinct from `format_command`'s "command has ..."). Counting the whole body means the `/cmd` path (body already `>seq cmd`) counts the seq, matching SPEC 2.3.

Test `test_send_refuses_a_line_over_the_twelve_token_cap` (full stack, `stack` fixture): 12 tokens -> 200, 13 tokens -> 400 asserting "13 tokens" and "cap".

Note on the brief: it states `format_command` already refuses on the `/cmd` path, while the sim-protocol leg's L7 evidence says neither path enforced it. `format_command` in the tree now does carry the cap (batch E's edit, `protocol.py:253-257`), so the brief is correct as of now; no contradiction to resolve.

## Revert verification

Each mutation applied alone, its own test run, file restored (byte-identical, checked with `diff -q`).

| Item | Mutation | Mutant | Fixed |
|---|---|---|---|
| CD2 | close back to `asyncio.to_thread` | FAIL (timeout, 7.15 s) | pass |
| CD6 | both pre-checks deleted | FAIL (AssertionError, not PortError) | pass |
| RG-F4 | `status()` back to `self.device if ... else self.serial_number` | FAIL | pass |
| class 39 | `try/except StoreError` removed | FAIL ("Task exception was never retrieved" in the handler) | pass |
| SP-L7 | token check deleted | FAIL (13-token /send returns 200) | pass |

## Gates

`uv run python -m pytest tests/test_review_r2_serial.py tests/test_reconnect.py tests/test_source_link.py -q` -> 70 passed, 1 skipped.
`uv run python -m ruff check mcuscope/serial_link.py tests/test_review_r2_serial.py` -> clean.
No em or en dashes in the touched files.
