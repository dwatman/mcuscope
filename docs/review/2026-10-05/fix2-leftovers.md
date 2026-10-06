# Fix batch: leftovers

## FD-STORE-3 server half
- `server.py` `_resolve_window`: one `anchor = store._window_anchor(bound)`; `floor_ts = anchor - last_ms/1000`; `scope["ceil_ts"] = anchor` (None without `last_ms`). The retrospective `/assert` unpacks and forwards `ceil_ts`.
- Test: `tests/test_server_window_ceiling.py` (5 rows stamped now+3600, `last_ms=60000`, through `/assert` and `/lines/export`, each with an unwindowed positive control).
- Revert-verify: `ceil_ts: None` in `_resolve_window` fails both; `None` in the `/assert` scope fails the assert test only.

## FD-STORE-6 wording
- `docs/SPEC.md` 3.3 example comment, `host/contrib/config.example.toml`, `webui/index.html:240` hint ("never deleted by age once ended"). README.md untouched. No test pins the hint text.

## config.py / update_check.py 0600/0700
- `config.write_new_file(tmp, data, like)`: tmp created 0600, or with the mode of the file being replaced when it exists (no chmod of existing files; a replace keeps the user's mode). `_write_doc` and `UpdateChecker._save_cache` use it and `dirs.make_private_dirs`.
- SPEC 3.2 item 6 "At start" list: one bullet for config.toml and the cache.
- Test: `tests/test_config_private_files.py`, umask 022, POSIX only (4 tests incl. existing directory keeps mode, replaced file keeps 0640/0644).
- Revert-verify: file mode ignored (0666), config mkdir, cache mkdir, cache plain write_bytes: each fails a test.

## Existing tests edited
None.

## SPEC edits
- 3.2 item 6: config.toml and update cache created 0600/0700.
- 3.3 example: min_sessions comment (ended sessions; the running one ages out).

## Changelog
- config.toml and the update-check cache are created owner-only on POSIX.
- `last_ms` on exports and retrospective `assert` ignores rows stamped after the window anchor.

## Not done
None.

## Doubts
- A replace keeps the old file's mode, so an existing 0644 config stays 0644 (by design: never chmod existing).
- Windows not run; the tests skip there. Ran only the touched files plus test_server_scope, test_config*, test_update_check* (159 passed); ruff clean on touched files.
