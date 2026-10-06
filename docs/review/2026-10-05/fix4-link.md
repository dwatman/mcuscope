# fix4-link report

## V93-1 unstorable episode open at stop
- `serial_link.py` `stop()` calls `_close_unstorable(stopping=True)` when `_unstorable_n`; `_close_unstorable` takes `stopping` and passes it to `_spawn_sys`.
- Test: `test_serial_link_rx_ingest.py::test_an_open_unstorable_episode_is_recorded_at_stop`.
- Revert-verify: replacing the call with `pass` fails it.

## V93-2 stranded-lines row
- `stop()` logs the row text at warning before spawning the row.
- Test: `test_stranded_lines_are_logged_not_only_recorded`; removing the log fails it.

## V95-1 typed names at ingest
- Cap now applies to every plot decode (`!p`, `!ps`), same `plot_names` set, `plot_name_refused` counter and once-per-attachment sys row (text now "distinct plot names (!p, !pd, !ps)").
- A `!pd` is not refused itself (it yields no points); the first `!ps` sample using a name past the cap is stored as a plain event.
- Test: `test_typed_names_count_against_the_same_cap`; narrowing the condition back to `!p` fails it.

## V95-3 `_carried`
- `PortManager.carried_evicted` counts evictions; the first logs a warning naming the alias.
- Test: `test_reconnect.py::test_carried_counters_are_bounded_and_evict_the_oldest` (count and one-warning). Mutation `if False` fails it.

## Existing tests edited
- `test_serial_link_rx_ingest.py::test_the_port_admits_a_bounded_number_of_names`: typed sample with a new name is now refused (count 3, `v` not plotted).
- Same file, `test_the_name_cap_notice_is_written_once_however_lines_interleave`: refused count 12 (typed `w` sample is refused each loop); notice text "distinct plot names".

## SPEC edits
- 2.2: open episode recorded at detach/reconnect/shutdown and logged.
- 2.5: cap covers `!ps` channel names; refusal wording.

## Changelog
- Plot-name cap (256 per port) now covers typed-stream channel names.
- An unstorable-line episode open at detach or shutdown is recorded; stranded-line losses are also logged.

## Not done
- SPEC ~735 (`plot_name_refused` "the `!p` lines refused") and `cli.py:266` comment, `cli.py:3158`: say "plot lines (`!p`, `!ps`)"; not my sections/files.
- Store batch: the 256-per-port prune of typed keys in `_plot_summary` is theirs; ingest names (all-time per run) can exceed what the summary keeps, by design (OP-2: 256 most recent).

## Doubts
- Refusing `!ps` samples (not the `!pd`) departs from the sweep's "refuse a `!pd`"; a refused sample's def stays in the decoder cache. Verified: ruff clean, ingest/framing/attach/reconnect selections pass. Not run: full suite.
