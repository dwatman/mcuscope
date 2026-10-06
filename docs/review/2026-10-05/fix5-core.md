# Fix 5, core (FD-CORE3-1 to 7), 2026-10-05

Done by the orchestrator directly: the batch agent's launch was denied by the auto-mode classifier with the owner away.
Each change was revert-verified (the file restored from a copy and checked with `cmp`).

- **FD-CORE3-1**: `server.py` routes every remaining client value through `_excerpt`: the unknown plot channel list, the five `_parse_deadband` refusals, `PUT /config/server`'s host error and `PUT /config/ports`'s device error.
  - Remaining f-string errors in `server.py`, each bounded: aliases (`ALIAS_RE`, 32 characters), session ids (typed int), `can id out of range` (parsed hex), regex compile errors (position, not the pattern), server-side exceptions.
  - Tests: five new rows in `test_server_error_excerpts.py` (`plot_channel`, `deadband_shape`, `deadband_name`, `host`, `device_scheme`); all five fail against the pre-fix `server.py`.
- **FD-CORE3-2**: `test_server_db_identity.py::test_an_in_memory_capture_keeps_its_export_copies_out_of_the_working_dir`, through the lifespan, with a positive control for a relative path; fails with the `:memory:` branch removed.
- **FD-CORE3-3**: `store.CaptureLocked` (a `StoreError`): `_delete_chunks` calls `_sweep_refused` for a busy error, which rolls back and opens the lock episode, and raises it; `server.py` `_store_error` answers it 503 with no traceback.
  - Covers purge and `DELETE /sessions/{id}?data=true` (both use `_delete_chunks`).
  - Tests: `test_server_purge_locked.py` (503, message, `db_locked_since` set, no record with `exc_info`; positive control: a disk I/O error stays a logged 500). Each half mutated alone fails it.
  - Existing test edited: `test_store_sweep_lock.py::test_a_purge_cut_short_names_what_it_had_deleted` now expects `CaptureLocked`, the new wording and the open episode.
- **FD-CORE3-4**: `_sweep_row` logs the row text with "not recorded in the capture: <why>" when it cannot be queued; the count stays pending. Test `test_a_sweep_row_that_cannot_be_queued_at_stop_is_still_logged`.
- **FD-CORE3-5**: `_trim_caps` replaces `_trim_cap`: a count carried into a sweep under another cap names both ("under the X and Y byte caps"). Test `test_a_folded_trim_row_names_every_cap_its_lines_were_trimmed_under`.
- **FD-CORE3-6**: `write_new_file`'s docstring now says what happens to a stale temp; no behaviour change.
- **FD-CORE3-7**: a lazy per-port name count (`_plot_counts`, reset on every delete and rebuild so it cannot drift) gates `_keep_recent_names`, so a new name under the cap scans nothing. Test `test_a_new_name_under_the_cap_scans_no_other_port` (64 ports x 4 names plus one port at the cap: zero prunes, then one for that port).

## Changelog

- A purge or session delete refused by another process's lock answers 503 ("the capture is locked by another process") with the count already deleted, and `/status` shows `db_locked_since`.
- Error messages from `/plot/export`, `PUT /config/server` and `PUT /config/ports` cut long client values to 80 characters.

## Doubts

- FD-CORE3-4 logs on every failed attempt while the write queue stays full; a full queue is itself a rare, announced condition.
