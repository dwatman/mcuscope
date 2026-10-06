# Fix report: link batch

Tests run: single files only (serial_link*, plot_grammar_fixture, protocol*, store_fastpaths, plot*, reconnect, e2e, wait_repeat, break): all pass. `ruff check .` clean. Revert-verified with `~/tt-data/mcuscope-2026-10-05/fix-link/mut.py` (12 mutations, all caught; sources restored and diffed against the `.good` copies).

## Per finding

- FUZZ-1: `serial_link.py` `_submit_rx_line` folds the line with `store.fold_breaks` before `classify` and every decoder.
  - Pinned by `tests/test_serial_link_rx_ingest.py::test_ingest_decodes_the_text_that_is_stored`, driven from the new `ingest` section of `tests/plot_grammar_cases.json` (`\r\r\n`, mid-line CR, for `!p`, `!pd`, `!can`).
  - Mutation (fold removed): caught.
- RES-3: `_drop_rx_line` no longer writes a sys row (`_unstorable` `_EpisodeNotice` removed). It keeps count, first/last time and last reason; `_close_unstorable` writes one row when a line next stores ("port X: N rx lines could not be stored between HH:MM:SS and HH:MM:SS: reason"). Logs the first drop and the closing count only.
  - Tests: `test_an_unstorable_episode_is_written_when_a_line_stores_again` (no row mid-episode, row after, second episode, singular), `test_a_settle_failure_also_counts_in_the_episode`.
  - Mutation (close disabled): caught.
- AGENTUX-9 `send` echo: `SerialPort.send_raw` already returned the stored tx row, so no code change. Pinned by `test_send_raw_returns_the_tx_echo_row`. The server must put `row["id"]` into the `/send` reply as `line_id`.
- OP-5: `rx_replaced` (lines with a byte above 0x7F) on `status()`, carried across re-attach, one sys row per episode, episode ends on a burst with none.
  - Tests: `test_a_replaced_byte_is_counted_and_announced_once_per_episode`, `test_a_clean_ascii_line_is_not_counted_as_replaced`, `test_the_replaced_and_refused_counters_survive_a_re_attach`.
  - Mutations (count removed, clear removed, carry removed): caught.
- OP-2: `protocol.PlotDecoder` admits at most `ADHOC_NAMES_MAX = 256` distinct ad-hoc names, all or nothing per line. `adhoc_refused` counts refusals; `adopt` carries the name set. The port counts `plot_name_refused` (on `status()`, carried), stores the line as an event, one sys row per episode, ended by a `!p` line that plots.
  - Tests: `test_the_decoder_admits_a_bounded_number_of_names`, `test_a_re_attached_decoder_keeps_the_names_it_has_seen`, `test_a_name_past_the_cap_is_stored_as_an_event_counted_and_announced_once`.
  - Mutations (cap removed, adopt removed, clear removed, counter removed): caught.
- OP-11.2 Python half: `PlotChannel.divisor` (k when 1/scale is an integer with 1 <= |k| < 2^53), set in `_parse_channel_spec`; `decode_plot_sample` divides by it, else multiplies. `parse_plot_def` equality unchanged apart from the new field.
  - Pinned by 7 new `sample` cases with a `values` key in `plot_grammar_cases.json`, asserted in `test_plot_grammar_fixture.py` (JS ignores `values` until the webui batch reads it).
  - Mutations (divisor branch, zero guard, 2^53 bound): caught.

## Existing tests edited

None.

## SPEC edits

- 2.1: the daemon decodes the folded text.
- 2.2: U+FFFD replacement counted by `rx_replaced`, one sys row per episode; unstorable-episode row written when a line stores again.
- 2.5: 256 ad-hoc names per port, overflow stored as an event, `plot_name_refused`, one sys row per episode; exact-decimal scaling rule.

## Guide wording

Not a server/store batch, but cli needs: `mcu ports` shows `rx_replaced` and `plot_name_refused` when non-zero (cli.py:216 prints only `dropped=`).

## Changelog

- A received `!p`, `!pd` or `!can` line with a stray CR now decodes the same as when replayed from the capture.
- Lines the capture could not store are now recorded as one sys row per episode, written once storage recovers.
- Bytes above 0x7F: `rx_replaced` counter and one sys row per episode.
- At most 256 distinct ad-hoc `!p` names per port; later names stay as text, counted in `plot_name_refused`.
- Scales like `*0.1` give exact decimals (`8.7`, not `8.700000000000001`).

## Not done

- webui batch: mirror the divisor rule in `plots.js` (`k = 1/scale`; if `Number.isInteger(k) && Math.abs(k) >= 1 && Math.abs(k) < 2**53`, `v /= k`, else `v *= scale`, guarding `scale === 0`) and assert `values` in `plot_grammar.test.mjs`. Cases are in the shared fixture, not yet run in node.
- server batch: `/send` reply `line_id` from `send_raw`'s row; `/status` surfaces `rx_replaced` and `plot_name_refused` already via `port.status()` (check it passes the dict through); `/plot/channels` limit/truncated.
- store batch: the summary rebuild at start must keep the 256 most recent names per port; the decoder starts empty on a restart, so a restart can admit 256 new names on top of the rebuilt set.
- cli batch: the `mcu ports` fields above.

## Doubts

- `rx_replaced` counts lines, not bytes; SPEC says so ("lines that carried one"). A bytes count was not asked for.
- An open unstorable episode at port stop or daemon exit with the store still failing is never written (no store to write it to); `rx_dropped` still counts it.
- The name set is per `SerialPort`, kept across re-attach (`adopt`) but not across a detach followed by a fresh attach; I did not check whether detach-attach should reset it.
- The unstorable timestamps use wall-clock `time.time()` at the drop, not the line's own rx timestamp.
