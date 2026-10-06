# Fix report: edge2 batch

Tests: `test_serial_link_rx_ingest.py` 16 passed; `test_cli_output_line_decoder_names.py`, `test_plot_grammar_fixture.py`, `test_store_plot_summary_cap.py` 94 passed; a neighbour-file run meant for serial_link*, protocol*, plot*, cli_output* and friends ran the whole pytest suite by mistake (a trailing space made the file list expand to a bare `tests/`), against the brief: 3281 passed, 2 failed (`test_port_health.py::test_last_ms_is_fixed_before_paging`, `test_cli_daemonctl.py::test_daemon_start_reports_an_unusable_data_dir_without_spawning`). Both fail the same with my serial_link.py and protocol.py swapped back, so they belong to other batches' in-flight work (CLI paging `id_to`, daemon pid-file wording). Log: `~/tt-data/mcuscope-2026-10-05/fix2-edge/py.log`.
`node --test plot_grammar.test.mjs` 5 passed. Firmware `make run` and `make asan` 323/323, `make families` green. `ruff check .` clean.
Mutations: `~/tt-data/mcuscope-2026-10-05/fix2-edge/mut.py`, 13 mutations of serial_link.py, all caught; source `cmp`-restored.

## FD-EDGE-4, 3, 1, 8: the name cap moved into SerialPort

- `protocol.py` `PlotDecoder`: the cap, `_adhoc_names`, `adhoc_refused` and the name merge in `adopt` are gone; the decoder is back to its pre-round shape. `ADHOC_NAMES_MAX` stays in protocol.py (the store's summary cap uses it).
- `serial_link.py:372` `SerialPort.plot_names`; `:988-1000` the cap applies to `!p` lines only, all or nothing per line, at ingest.
- FD-EDGE-8: names join the set only after the row is queued (`:1017`), and `_settle_rx_line` gives them back when the stored row fails (`:1025`, carried in `_RxPrep.names`).
- FD-EDGE-3: `plot_names` rides in `PortManager._carried` beside the counters, so a detach/attach and a replacing attach both keep it.
- FD-EDGE-1: the notice is never cleared: one sys row per attachment (a re-attach is a new `SerialPort` and announces once more, as the other shed notices do). Text ends "(counted in plot_name_refused)".
- Tests (`test_serial_link_rx_ingest.py`):
  - `test_the_port_admits_a_bounded_number_of_names` (all or nothing, known names still plot, malformed and typed lines not counted), replaces the two decoder-level tests.
  - `test_the_name_cap_notice_is_written_once_however_lines_interleave` (the FD-EDGE-1 repro: steady `!p`/`!ps` between new names, one row), replaces `test_a_name_past_the_cap_is_stored_as_an_event_counted_and_announced_once`.
  - `test_a_line_the_store_refuses_spends_no_name_slot` (submit raise and failed future, with a positive control).
  - `test_the_counters_and_the_name_set_survive_a_re_attach` (detach/attach and replacing attach), replaces `test_the_replaced_and_refused_counters_survive_a_re_attach`.
  - FD-EDGE-4: `test_cli_output_line_decoder_names.py` decodes a 300-name history.
- Revert-verified: cap removed, admit removed, give-back removed, admit before submit, carry dropped, latch cleared on plot, refused line admits its names, refused not counted, typed streams counted: each caught. FD-EDGE-4 test fails against the pre-batch protocol.py.

## FD-EDGE-2: NOT DONE, owner choice needed

The browser cannot re-derive the daemon's admitted set, so the stored row needs a mark, and a stored row carries only `id ts port dir chan seq raw`.
- Within my files the only mark is the chan: store a refused `!p` as chan `debug` (plotIngest returns on any chan but `event`). Costs: `--chan event` filters (`mcu tail`, `mcu wait`, `/lines`) no longer find those lines, and SPEC 2.x's "a `!` line is an event" gains an exception. No new chan value is possible (DB CHECK constraint).
- The alternative is a store column (or server-computed field) such as `plot_refused` on `/lines` and WS rows, with plotIngest skipping it: store, server and webui batches, plus a schema change.
- Either way the shared case is an `ingest` fixture case for a refused line that Python ingests (asserting the row's mark) and JS feeds as that row (asserting no chart).

## NITs

- FD-EDGE-5: `|k| >= 1` deleted in `protocol.py:_scale_divisor` and `plots.js:254`; SPEC 2.5 says "magnitude below 2^53". A deleted redundant clause: no test can catch it, the fixture's `values` cases still pass in both engines.
- FD-EDGE-6: `monitor.c` `monitor_init` resets `g_ovf_count` unconditionally. Re-measured with the firmware batch's matrix (APP printf, its baseline reproduced exactly) and a savings script (`~/tt-data/mcuscope-2026-10-05/fix2-edge/fp/`):
  - core 4445/4489/6004 -> 4429/4473/5992 B, plot 7325/7377/10356 -> 7309/7361/10344 B (M0+ -Os / M4F -Os / M0+ -O2), RAM unchanged (968 / 1132).
  - Only the M0+ -O2 plot figure moves at KB rounding (10.4 -> 10.3 KB).
  - Flash savings per `MON_NO_` switch unchanged; the RAM saved by `MON_NO_CAN` and by all five reads 20 B, was 16 B (`.bss` padding: the core build now links `g_ovf_count` and its total stays 968).
- FD-EDGE-7: the `tag == "!p"` clear is gone with FD-EDGE-1. `test_an_unstorable_episode_names_its_first_and_last_drop` patches the clock and raises a message-less exception, asserting the whole row; mutations "first stamped on every drop", "last never stamped", "fallback dropped": each caught.
- FD-EDGE-9: the U+FFFD row reads "port X: received bytes above 0x7F, stored as U+FFFD and counted in rx_replaced; a baud mismatch looks like this", asserted whole in `test_a_replaced_byte_is_counted_and_announced_once_per_episode` (mutation caught).

## Existing tests edited

- `test_serial_link_rx_ingest.py` (a link-batch file, this round): the three replacements above, and the U+FFFD notice asserted in full.

## SPEC edits

- 2.5: the cap is kept across detach/re-attach, an unstored line spends no slot, one sys row per attachment, the cap is ingest-only (stored-row decoders decode every name); scale rule "magnitude below 2^53".
- 5.1: M0+ -O2 plot 10.3 KB; `MON_NO_CAN` saves 20 B RAM.
- INTEGRATION.md: the same two figures, and the all-five RAM saving 20 B.

## Changelog

- `mcu tail --decode` and other stored-row decoding no longer stop at 256 ad-hoc names.
- The 256-name cap survives a detach and re-attach of the port, and is announced once per attachment instead of every time a new name follows a plotted line.

## Not done

- FD-EDGE-2, pending the choice above.

## Doubts

- `_settle_rx_line` gives back a failed line's names even if a later line in the same burst reused one and stored; that name then has rows but no slot until it is seen again. Needs a per-line store failure with a later success in one burst.
- The timestamp test patches `time.time` globally (serial_link's `time` is the module); it relies on the first three calls being the drops, which held deterministically.
