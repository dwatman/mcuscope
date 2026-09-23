# Capture pipeline integrity (aspect `capture`)

Branch `review/2026-09-23-opus55` at `6e4f6f7`.
Probes and their daemons' logs/DBs are in `~/tt-data/mcuscope-2026-09-23/capture/` (`h.py` is the harness: a TCP fake board plus an isolated `mcuscoped --config` subprocess).

## CAPTURE-1 HIGH CONFIRMED: `ts` is not monotonic in `id`, so `since_ts` / `last_ms` windows and `purge before_ts` silently drop or over-delete rows, with no clock step

- `store.py:1622` `_window_id_floor`, `store.py:1820` `last_id_before_ts` (used by `server.py:1613`), both documented as "assumes `ts` rises with `id` ... the host stamps every line at receive time on the single writer" (`store.py:1638`, `store.py:1828`).
- That premise is false. `ts` is stamped per burst in each port's reader thread (`serial_link.py:587`), and sys/marker/tx rows at submit time. `id` is assigned later by the writer, in whatever order the per-port consumers reach it.
  Two ports (or one backlogged port plus markers or commands) interleave, so `ts` goes backwards as `id` rises.
- Failure: the id floor is the id of the lowest-`ts` row above the cutoff. Any row with `ts` above the cutoff but a smaller id falls below that floor and is never returned.
- Repro `p10_ts_order.py`: two fake boards flooding, 20 markers posted meanwhile, then `/lines/export?format=jsonl&since_ts=X` compared with the exact set `ts > X` from `/lines`.
  - Flood: `ts decreases with rising id at 15 places, worst 1489.5 ms`, `since_ts cutoffs with rows missing: 6/39`; the worst one dropped 1000 rows (ids 16510+, `A0090000...`).
  - Bench rate, 2 ports x 500 lines/s (`p10b_ts_order_paced.py`): 1 inversion of 1.5 ms, and 1 of 399 cutoffs dropped a whole 20-line burst.
- The same premise drives `purge before_ts`: it deletes the id range `1..last_id_before_ts(T)`, so rows newer than T that sit below that id are deleted too, and older rows above it survive.
  Repro `p9_purge_clockstep.py` (a real `Store`, the handler's two calls, `ts` out of id order): `lines older than cutoff: 70; purge id range 1..110; deleted 110`, `40 lines at or after the cutoff were deleted`.
  The dry run reports 110, so the count shown before deletion is wrong in the same way.
- Consequences an agent acts on:
  - A retrospective `/assert` with `last_ms` can miss a line and pass a `forbid`.
  - `mcu lines --last-ms` and exports with `since_ts` omit rows.
  - A purge deletes lines newer than the cutoff it was given.
- SPEC 3.4 (line 870) admits the floor is inexact, but only across a wall-clock step; this happens in normal operation.
- Fix: derive the floor exactly, as the `until_ts` ceiling already is (`_window_id_ceiling`).
  That means `SELECT MIN(id) FROM lines INDEXED BY idx_lines_ts WHERE ts > ?`, a covering-index walk.
  Or seek the floor at `cutoff - slack` and keep the `ts` term, which stays exact while the inversion is shorter than the slack.
- For `before_ts`, delete `WHERE ts < ?` in chunks (retention's `_delete_expired_chunk` already does this) instead of an id range.
- Known class: related to class 20 (the id floor is the sargability fix), but the premise itself is new.

## CAPTURE-2 MEDIUM CONFIRMED: session rollover leaves an uncovered gap; lines in flight belong to no session

- Location: `store.py:1258`. `start_session` closes the running session (its end marker gets id E, and `end_id = E`), then `await self.drain_writes()`, and only then samples `start_id = _next_id`.
  Lines committed during that drain get ids between E+1 and `start_id-1`, which lie in neither session.
  `POST /sessions/stop` (named session stopped, then an automatic one opened) runs the same two calls under two separate lock acquisitions.
- SPEC 3.4: "the capture is always covered by exactly one" session.
- Repro `p3_session.py`: 60k lines at about 10k lines/s, with `POST /sessions` every 100 ms.
  Result: `sessions 58 ... orphans 1600 overlap 0`, 8 gaps of 200 lines each, e.g. `('run7', 8420, 8621)`.
  An orphan (id 8421) is found by no session-scoped `/lines` (`found in session-scoped /lines of: []`).
- Consequence: `session export`, `bundle`, `--session` queries and `/assert session=` all silently miss the boundary lines. A board reset at run start is exactly what lands there.
- Fix: on rollover, open the new session at `end_id + 1` (sample `start_id` from the end marker's row, not after a drain), so the spans abut. The drain is only needed when no session was running.

## CAPTURE-3 MEDIUM CONFIRMED: the id sequence can move backwards after the newest lines are deleted, with no capture-token change

- Locations: the resync `_next_id = max(_max_id_sql, _max_session_ref_id) + 1` in the commit-failure path (`store.py:812`) and in `_insert_individually` (`store.py:916`).
  `_insert` also lets SQLite pick the id (max rowid + 1).
- After a purge that removed the newest rows, SQL's max id is below `_next_id - 1`, the value `max_id()` has already given clients. One failed commit (disk full, I/O error) or one row-by-row fallback then rewinds the sequence below ids clients have already seen.
  The capture token rotated at the purge, not at this rewind.
- Repro `p5_commitfail.py`:
  1. Ingest 1000 lines (top id 1003), then `POST /purge {all}`.
  2. Arm `/wait ^AFTER` (watermark 1003).
  3. Lower the daemon's soft `RLIMIT_FSIZE` so one burst's commit fails (`batch commit failed: disk I/O error`), then restore it.
  4. Send `AFTER 1`/`AFTER 2`.

  Result: they are stored as ids 3 and 4; `/wait` answers `timeout` (CLI exit 2) though the match arrived; `/lines?since_id=1003` returns `[]`.
- Impact is HIGH-shaped: a silent wait timeout, and followers blind until ids pass the old top. The trigger (a failed write as the first write after a tail delete) is narrow, hence MEDIUM.
- Fix: never lower `_next_id` on resync: `max(self._next_id, sql_max + 1, session_ref + 1)`. Rolled-back ids become a harmless gap.
  Have `_insert` bind the id explicitly from that sequence instead of letting SQLite choose.
- Known class: none exact; related to class 77 (a sequence fix-up that loses monotonicity).

## CAPTURE-4 MEDIUM CONFIRMED: an oversized line's tail is stored as a complete line when it arrives in more than one read; the drop count is per 4 KB, not per line

- Location: `serial_link.py:744`. The unterminated-buffer cap clears the partial line, but the rest of that same line keeps arriving. When its LF comes, the tail is split out and stored as an ordinary line (classified, so a tail starting `<`/`!` is parsed as a response or event).
  The same bytes delivered in one read are dropped whole (`serial_link.py:775`), so the outcome depends on read timing.
- Repro `p1_framing.py` (fake board, 512-byte writes 20 ms apart):
  - `long4097-chunked got ['']`: an empty row stored.
  - `long8000-chunked got [<3392 chars: 'xxxx...'>]`: a fragment stored.
  - The `-one` (single write) variants store nothing.
- Repro `p13_longline_count.py`: one 20000-byte line in 512-byte reads gives `rx_dropped 4` and a 1568-char fragment queued as a line.
- Fix: after a cap drop, set a "discarding" flag that swallows bytes up to and including the next LF, and count the line once.

## CAPTURE-5 MEDIUM CONFIRMED: the startup size-cap trim writes no sys row

- Location: `store.py:589`. `_initial_sweep` calls `_sweep_size_async()` directly. The sys row SPEC 3.2 requires ("record a `sys` row saying how many were lost") is written only in `sweep_tick` (`store.py:2751`).
- Repro `p11_startup_trim.py`:
  1. Capture 5.5 MB with no cap.
  2. Restart with `max_db_bytes = 1048576`.

  Result: `lines_trimmed 24903`, the daemon log says `trimmed 24903 oldest lines`, and `sys rows mentioning trim: []`.
  Lowering the cap in config and restarting is the ordinary way to trigger it, and it is the largest trim a capture ever sees.
- Fix: have `_initial_sweep` go through the same helper that writes the row (or through `sweep_tick`).
- Known class: 38 (a re-run missing the first run's discipline).

## CAPTURE-6 LOW CONFIRMED (latent): lines popped into a consumer batch are lost uncounted when detach cancels the consumer on store back-pressure

- Location: `serial_link.py:812`. `_consume` pops up to 1000 lines into a local `batch`. If `stop()` cancels it while `_submit_rx_line` awaits `store.submit_line` (store queue full), the rest of the batch is neither stored nor counted.
  `stop()` counts only what is still in `_rx_lines`.
- Repro `p6_cancel_batch.py` (real `SerialPort` over a `SourceLink`, a stub store whose queue is full): 500 lines received, `lines_rx 1`, `rx_dropped 0`, and no sys row. The 499 lost lines leave no trace.
- Latent: with the real store, one port holds at most one batch in the writer queue, so this needs about 10 flooding ports or a stalled writer.
- Fix: keep the unsubmitted remainder on `self` (or push it back to the front of `_rx_lines`) so `stop()` counts it.

## CAPTURE-7 LOW CONFIRMED: a partial line at disconnect or detach vanishes with no count and no row

- Location: `serial_link.py:704` (`_rx_bytes.clear()`), and `stop()` for the detach case.
- Repro `p7_disconnect.py`: `PARTIAL-0-` then a TCP drop gives `rx_dropped delta 0`; the text appears nowhere. Dropping it is right (it would corrupt the next line), but it is the one shedding path with no counter.
- Fix: when the buffer is non-empty, count it in `rx_dropped` and mention the byte count in the existing "disconnected" row.

## CAPTURE-8 LOW CONFIRMED: more than one trailing CR is stripped

- Location: `serial_link.py:778`. `.rstrip("\r")` removes every trailing CR; SPEC 2.1 strips "a preceding `\r`".
- Repro `p1_framing.py`: `a\r\r\n` is stored as `a`. Per SPEC plus the store's CR fold it should be `a ` (trailing space).
- Fix: `line[:-1] if line.endswith(b"\r") else line`, before decoding.

## CAPTURE-9 LOW CONFIRMED: the text export splits rows on line boundaries other than CR/LF

- Location: `store.py:1003` folds only CR and LF. VT, FF, FS/GS/RS (reachable from the wire) and NEL, U+2028 (reachable through `POST /marker`) survive into `/lines/export?format=text`.
- Repro `p12_exports.py`: 15 rows export as 15 `\n`-separated lines but 23 by `str.splitlines()`. jsonl, csv and the session DB export are exact.
- Fix: fold the `str.splitlines()` boundary set in `_write_req` too, or escape them in `fmt_line` only.

## Checked and fine

- Split at every byte offset of a 3-line message (31 offsets), CRLF, CRLF split between reads, lone CR (folded to space), empty lines, NUL, DEL, ESC sequences: exact (`p1_framing.py`).
- High bytes and UTF-8, including a multi-byte char split across reads: each byte becomes U+FFFD, deterministically and independent of the split. This is lossy by design (SPEC 2.1 allows rejecting >0x7F).
- Lines of 255, 256, 4095 and 4096 bytes, in one write or chunked, are stored exactly. 4097 and 8000 bytes in one write are dropped and counted (`p1_framing.py`).
- 200k-line flood with random write sizes, mixed debug, `!can`, `!p`, `!m` and stray `<N OK`: no unknown rows, no duplicates, no inversions. `stored + rx_dropped == sent` exactly (188,560 shed to the documented drop-oldest, with one overflow sys row).
  Paced at about 19k lines/s: the same identity holds.
  jsonl, csv and text exports match `/lines` row for row (`p2_burst.py`).
- Complete line immediately before EOF is stored, and a partial line is not glued onto the first line after reconnect, over 3 cycles (`p7_disconnect.py`).
- Size-cap trim racing a paced 60k-line ingest:
  - What remains is the exact contiguous newest suffix.
  - `rows kept + lines_trimmed == last id`.
  - A downward `id_to` pager ran 116 passes during it with every pass unique and sorted (`p8_trim.py`).
- `purge` of the newest ids rotates the capture token and the next batch continues above the old top (`p4_idreuse.py`).
  A lone-surrogate `/marker` is refused with 422 before the store, so it cannot trigger CAPTURE-3's fallback.
- Session DB export of an ended session equals the session-scoped `/lines` exactly, NUL and control chars included (`p12_exports.py`).

## Not covered

- Native serial ports (the `in_waiting` drain branch), `rfc2217://`, and Windows.
- `/ws` fan-out fidelity, and `/can/frames` and `/plot/*` read-back against the stored children.
- Age retention racing ingest; purge `--session` of the running session while ingesting.
- Response matching (late/duplicate `<seq`) beyond classification.
- A real wall-clock step (needs sudo). CAPTURE-1's purge half was driven with injected `ts` on an in-process `Store`.
