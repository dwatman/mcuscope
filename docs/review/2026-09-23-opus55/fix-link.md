# Fix batch: link

Files changed: `host/mcuscope/serial_link.py` (`link.py` untouched: no finding needed it).
New tests: `tests/test_serial_link_rx_framing.py`, `tests/test_serial_link_tx.py`, `tests/test_serial_link_attach.py`.
Revert-verification: 20 mutants applied one at a time to a scratch copy of the tree (`~/tt-data/mcuscope-2026-09-23/fix-link/mut/run_mutants.py`, output `mutants.out`), never to the shared file; all 20 KILLED.

## CAPTURE-4: oversized line's tail stored as a line

- `serial_link.py:341` new `_rx_discarding`; `_on_bytes` (`:765-785`): a cap drop sets it, and bytes up to and including the next LF are swallowed. The line is counted once, when the cap trips.
- Sys row text is now `dropped an unterminated line after N bytes, over the 4096 byte cap; discarding to its end`. It is latched once per episode as before, and a discard's own LF does not end the episode.
- `_take_partial` (`:708`) clears the flag at disconnect and detach, so the first line after a reconnect is never mistaken for the tail.
- Tests: `test_an_oversized_line_in_many_reads_stores_no_tail_and_counts_once` (20000 bytes in 512-byte reads: `rx_dropped == 1`, only `after` queued), `test_the_discarded_lines_lf_as_the_first_byte_of_a_read`, `test_back_to_back_oversized_lines_report_once_until_a_clean_line`, `test_a_disconnect_ends_the_discard`.
- Revert: M1 (discard skipped), M2 (flag never set), M3 (flag not reset at LF), M4 (flag survives disconnect): each KILLED.

## CAPTURE-6: consumer batch lost on detach

- `serial_link.py:873-877`: a CancelledError in the submit loop pushes the unsubmitted remainder (including the line being submitted) back to the front of `_rx_lines`, where `stop()` counts it.
- Test: `test_lines_in_a_consumer_batch_cancelled_by_detach_are_counted` (stalled store, 500 lines: `rx_dropped == 500` and the `dropped 500 received lines not yet stored at detach` row).
- Revert: M11 KILLED.

## CAPTURE-7: partial line at disconnect or detach

- `_on_disconnect` (`:715-729`): counts a partial in `rx_dropped` and names it in the existing row, `port X disconnected (dropped a N-byte partial line)`. With no partial the row is unchanged.
- Detach: the disconnected row is withheld once stopping (existing rule), so `_on_disconnect` leaves the partial and `stop()` (`:441-460`) takes it into its own row. That row reads `dropped a N-byte partial line at detach`, or `dropped K received lines not yet stored and a N-byte partial line at detach` when both apply.
- Tests: `test_a_partial_line_at_disconnect_is_counted_and_named` (with a clean-disconnect positive control), `test_a_partial_line_at_detach_is_counted_and_named` (real reader over a `SourceLink`).
- Revert: M6 (disconnect takes the partial while stopping), M7 (uncounted), M8 (row unnamed), M9 (stop ignores it), M10 (stop names but does not count it): each KILLED.

## CAPTURE-8: every trailing CR stripped

- `serial_link.py:808`: `.removesuffix("\r")` instead of `.rstrip("\r")`. `a\r\r\n` queues `a\r`; the store's CR fold then makes it `a `.
- Test: `test_only_one_trailing_cr_is_stripped`. Revert: M5 KILLED.

## API-1 (link half): device writes on a private pool, stamped at the write

- `serial_link.py:48-52` `_write_pool` (`max_workers=2 * MAX_PORTS`, lazily spawned). `send_raw` (`:1148`), `send_break` (`:1177`) and `send_command` (`:1208`) run on it instead of `asyncio.to_thread`. `MAX_PORTS` moved up beside it.
- `_write_bytes` now returns `time.time()` taken under `_write_lock` just before `link.write` (`:1078`). `send_command` puts it in `pend.sent_ts`, so both the stored cmd row's ts and `latency_ms` start at the real write. `send_raw` stores its row at that ts instead of at `time.time()` after the write returned.
- The pending entry is still registered before the write, because the response can land before the write returns.
- Tests (`test_serial_link_tx.py`):
  - `test_device_writes_do_not_queue_behind_the_default_executor[send_raw|send_break|send_command]` starves the default executor to one occupied worker.
  - `test_a_queued_command_is_stamped_when_its_write_begins` and `test_a_raw_send_is_stamped_when_its_write_begins` hold `_write_lock` from another thread for 0.3 s, then assert `release <= row ts <= link.write entry`, plus a latency bound for the command.
- Revert: M12, M13, M14 (each call site back on the default executor), M15 (command stamped at registration), M16 (raw stamped after the write), M17 (stamp taken before the lock): each KILLED.

## LIFECYCLE-4: reconnect racing a detach

- `PortManager.attach(..., require_existing: bool = False)` (`serial_link.py:1359`). When true, it raises under the manager lock (`:1397`) if the alias is gone.
- It raises `PortError(f"no such port: {alias}")`, the same text the reconnect route already sends for a missing alias. The route's existing `except PortError as exc: return _bad_request(str(exc))` maps it to 400 `no such port: <alias>`.
- There is no unlocked pre-check. The route already refuses a missing alias before calling, so one would be redundant.
- Test: `test_a_reconnect_racing_a_detach_does_not_reattach` (prime held open, detach completes, reconnect refused, alias stays gone). It also has a positive control: with the alias present, `require_existing=True` replaces it.
- Revert: M18 KILLED.

## HEALTH-15 L03, L06

- L03: `test_a_second_oversized_episode_is_reported_again` (rx_framing file). Mutant (`_oversized.clear()` -> `pass`) KILLED.
- L06: `test_a_failed_tx_row_leaves_no_pending_entry` (tx file). Mutant (only the `_pending.pop` on tx-row failure removed) KILLED.

## HEALTH-20 SRC-8

- The comment at `send_raw` (`:1137-1139`) now says what the code does: `/cmd` strips surrounding whitespace (CR and LF included) in `format_command`, and `/send` is verbatim, so it refuses them.
- The behaviour is defensible: a command's tokens carry no surrounding whitespace, and a raw send must not become a different write. The `/send` message `line must not contain embedded newlines` also covers a trailing one. `test_port_health.py:604` pins that text, so I left it.

## HEALTH-26 stale comments

- `:1-8` module docstring now names `link.open_link` and the thread-shared state (`_link`, `_write_health` under `_write_lock`, the stop event).
- `:579-583` `self._serial` -> `self._link`.
- `_decode_plot` (old `:960-967`) inlined into its one caller (`:931-933`), which drops the false "counters and sys-row latch" claim. Nothing else referenced it.
- Also corrected, because this batch made them stale: the `stop()` close comment (writes are no longer on the default executor), and the `_write_bytes` and `send_raw` comments (`to_thread` / "executor workers").

## Verification runs

- The three new files: 16 passed. `ruff check` is clean on `serial_link.py`, the new files and `test_reconnect.py`.
- Every test file that imports `serial_link` was run one file at a time against the live tree (log `~/tt-data/mcuscope-2026-09-23/fix-link/files.log`).
  - All green: test_reconnect, test_port_health, test_prerelease_link_fixes, test_source_link, test_link, test_break, test_sim_tcp, test_sim, test_protocol, test_plot, test_plot_export_decode, test_prerelease_daemon_core_plot, test_config_api, test_store_writer.
  - My one failure is test_e2e `test_malformed_seq_response_resolves_fast` (see Not done).
- The rest fail from other batches' in-progress edits, not from this batch:
  - `Store.has_port_rows` and `_lines_index` are undefined, which breaks stack startup in test_security, test_decode_per_port and test_review_r2_serial.
  - `cli_argv.hoist_global_opts` is missing, and there are CSV-guard and unknown-parameter changes.
  - test_daemon_r2026_09_12_server, test_rulings_daemon_plot, test_hardening, and test_regressions `test_assert_with_send_still_judges_lines_the_send_used_the_whole_window_for` fail the same way on a scratch copy of the tree with the pre-batch `serial_link.py` swapped in.
  - I did not attribute the two test_store_fastpaths failures (`disk I/O error` path, summary counts); they are store-side.
- test_regressions.py has an I001 import-order ruff error. It comes from the cli batch's concurrent edit of that file's imports, not from my stub edits.

## Existing tests edited

Each `_write_bytes` stub now returns `time.time()`, since the real method returns the write stamp:

- `tests/test_port_health.py:308`, `loud._write_bytes`.
- `tests/test_reconnect.py` (the signature wrapped to stay inside 100 columns): the `write` stub in `test_a_disconnect_during_a_command_leaves_no_unretrieved_future`, and the `lambda` in `test_a_cancelled_or_timed_out_command_consumes_its_future` (the latter failed with a TypeError on `latency_ms`).
- `tests/test_regressions.py`: `blocked_write`, `blocking_write`, `slow_write`. The two `send_raw` ones otherwise store `ts=None` against a NOT NULL column.

## SPEC edits

None. SPEC 2.1 already says one preceding CR is stripped, and the host 4 KB cap is not in SPEC.

## Changelog

- An over-long received line that arrives across several reads is dropped whole and counted once; its tail is no longer stored as a line of its own.
- A partial line cut off by a disconnect or detach is counted in `rx_dropped` and named in the disconnect or detach sys row.
- Received lines lost when a detach interrupts a store that is behind are counted in `rx_dropped`.
- Only one trailing CR is stripped from a received line (SPEC 2.1).
- Device writes (`/send`, `/cmd`, `/break`) no longer queue behind session exports. A command's `latency_ms` and the stored cmd/tx row's ts start when the bytes were written.
- A reconnect racing a detach no longer brings the detached port back (with the server batch's caller change).

## Not done

- **tests batch, `tests/test_e2e.py:554`** (`test_malformed_seq_response_resolves_fast`): change `port._write_bytes = lambda data: None` to `lambda data: time.time()`. Until then that test fails with `NOT NULL constraint failed: lines.ts`. This is the only failure my change causes in the files I ran.
- **server batch, `server.py` reconnect route (`:1030`)**: call `ports.attach(alias, pt.device, pt.baud, pt.serial_number, pt.identify, pt.eol, require_existing=True)`. It is a new trailing parameter, so pass it by keyword; existing positional calls are unaffected.
- **server batch, SPEC 3.4 (`docs/SPEC.md:638`)**: add the new cause to the `rx_dropped` definition. Suggested: "shed under back pressure (SPEC 3.2 drop-oldest), over the line cap, refused by the store, or cut off mid-line by a disconnect or detach."
- **tests batch, `docs/ARCHITECTURE.md:36`**: says the default executor "joins the serial reader thread". The join has had `_join_pool` since 2026-08-01, and device writes now have `_write_pool`.
- **orchestrator, `docs/REVIEW.md` class 1**: the invariant could name device writes beside the join as work that never queues on the default executor. The behavioural test is `test_device_writes_do_not_queue_behind_the_default_executor`.
- `send_break`'s sys row is still stamped after the break ends, not when it began. Not asked for; say if it should use the write stamp too.

## Doubts

- Pool size: `2 * MAX_PORTS` assumes at most two writes in flight per port (one per asyncio lock). A cancelled request releases its asyncio lock while its worker is still blocked in the driver, so a client that repeatedly cancels sends against a stalled port can hold more workers. Each is bounded by WRITE_TIMEOUT and serialised on `_write_lock`. Other ports then queue only once all 64 are held.
- The detach-partial test relies on the reader's `_on_disconnect` running before `stop()`'s accounting. Both orders produce the same row by design: if it runs after, `stop()` already took the partial. Only the first order is driven.
- `lines_rx` counts the line whose submit was cancelled, and `rx_dropped` counts it too. That matches `_drop_rx_line`'s existing double count, but I did not check what SPEC says `lines_rx` means.
- The `stop()` row says "at detach" for `hold()` (disconnect on request) as well. The wording predates this batch.
- Not checked: Windows, and a real serial device. All of it ran through `SourceLink` or duck-typed link stubs.
