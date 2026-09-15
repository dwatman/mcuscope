# Fix batch: daemon (FD2-1, FD2-2, FD2-3, FD2-4, FD2-6, FD2-7)

Base: 5489d6e. FD2-5 is the owner question and was left alone.
Files touched: `host/mcuscope/server.py`, `host/tests/test_rulings_daemon_startlog.py`, `host/tests/test_rulings_daemon_config.py`, `host/tests/test_hardening.py`, the new `host/tests/test_fixdiff2_daemon.py`, and `docs/SPEC.md` 3.4.
Source copies for the revert legs: `~/tt-data/prerelease-2026-09-15/fix-fixdiff2-daemon/`; every file was diffed back to its copy after each leg.

## FD2-1: the bind-failure assertion was Linux-shaped

- `test_rulings_daemon_startlog.py:64-69`: `startswith("reason: [Errno")` becomes `re.match(r"reason: \[(Errno|WinError) \d+\] ", reason)` plus `str(port) in reason`.
- The reason uvicorn logs is `[Errno 98] error while attempting to bind on address ('127.0.0.1', <port>): ...`, so the port is in the line on both platforms; only the bracket word changes.
- MUTATED (no Windows host here, so the product branch was mutated instead of the platform changed), `daemon.py:294` `_FirstError.emit`:
  - `lines[-1].strip().split("] ", 1)[-1]` (bracket prefix dropped): the regex assertion fails, the port assertion still passes.
  - `lines[-1].strip().split(" on address ", 1)[0]` (address dropped): the port assertion fails, the regex assertion still passes.
  Each half is live and neither stands in for the other. `daemon.py` diffed identical to its copy afterwards.

## FD2-2: "refused before the pid claim" was asserted where no record ever is

- `test_rulings_daemon_config.py:254-281`: the `run_main` fixture wraps `pidfile.claim` with a spy and exposes `run_main.claims`; `test_a_named_config_that_does_not_exist_is_refused` now asserts `run_main.claims == []`.
- `test_rulings_daemon_config.py:284-293`: new `test_a_start_that_gets_past_the_config_does_claim` is the positive control in the same file: the served path records one claim ending `.pid`, and the data dir is still empty afterwards, which is the point.
- `test_rulings_daemon_startlog.py:93-118`: the same spy inline in `test_a_corrupt_capture_through_main`; the claim assertion is the positive control for the `*.pid` glob that follows it, which is kept (it still pins the release).
- MUTATED `daemon.py:368`, claim and release on the refusal path (`pidfile.release(pidfile.claim("127.0.0.1", 8558))` before the `ConfigError`): the spy assertion fails with the claimed path, while the old `glob("*.pid")` form would have passed, since the record was released. That is the class 78 shape, driven.
- MUTATED `daemon.py:424`, `pid_path = None` (no claim at all): both positive controls fail (`claims == []`), while the surviving glob assertion still passes on both paths.

## FD2-3: `/plot/channels` rescanned every detached port on every request

- `server.py:1785-1815`: `detached_meta` (alias -> learned channel meta) and `detached_capture` live in `_register_routes`, so one cache per app. The route clears the cache when `store.capture_id` changes, then serves a detached row's port from the cache instead of building a `PlotDecoder` and calling `learn_stored_plot_defs` per request. An attached port never reaches the cache: the manager's own meta wins.
- `server.py:1009-1011`: `POST /ports` drops the alias's entry, the one way a port goes from "not in the manager" to "in it" and then stores new definitions. (`/ports/{alias}/reconnect` cannot: it requires the port to already be in the manager.)
- Tests in `test_fixdiff2_daemon.py`: `test_a_detached_boards_definitions_are_learned_once` (spy on `server.learn_stored_plot_defs`; first call records one scan as the positive control, second records none, and both calls return the identical body), `test_replacing_the_capture_drops_the_learned_definitions` (purge-all mints a new capture id, the re-seeded board's new `B_OFF/B_ON` labels must be served), `test_attaching_the_alias_drops_its_learned_definitions` (attach to a dead `socket://` device, store a new definition, detach, and the answer must have moved from `A_RUN` to `B_ON`).
- REVERTED, one branch at a time, restoring from the copy after each: cache read removed -> `scans == ["far", "far"]`, learned-once test fails; `capture_id` clear removed -> the purge test fails with the old `A_IDLE/A_RUN` labels still cached; `detached_meta.pop` removed -> the attach test fails with "served from the meta learned before attach".

## FD2-4: two stored ports that sanitise alike wrote one zip member twice

- `server.py:1474-1490`: `_build_bundle` keeps a `port -> stem` map; a stem already taken by another port gets `-2`, `-3` before the stream id (`plot_a_b-2_3.csv`). The manifest is built from the names as written, so it still lists exactly the zip's entries.
- `docs/SPEC.md` 3.4 (line 808): one sentence for the disambiguation rule beside the sanitisation rule it fixes.
- Test `test_two_ports_that_sanitise_alike_get_distinct_bundle_members`: stored ports `a/b` and `a_b`, each with its own definition and one stream-3 sample; asserts the two member names, that no name repeats in `namelist()`, that `manifest["files"] == namelist()`, and that both boards' rows read back (`A_RUN,7.0` and `B_OFF,8.0`).
- REVERTED (stem line back to the plain sanitise): `namelist()` is `['plot_a_b_3.csv', 'plot_a_b_3.csv']` and the test fails, reproducing the duplicate member.

## FD2-6: the flag-beats-variable test could not observe its second run

- `test_rulings_daemon_config.py:262-266`: `run` clears `served` and `claims` before each run, so both halves report that run only.
- `test_rulings_daemon_config.py:298`: the second assertion is now `(1, False)` with the comment corrected.
- MUTATED `daemon.py:368`, `_serve(None, host="127.0.0.1", port=0)` before the `ConfigError`: the test fails on `(1, True) == (1, False)` with the exit code and the stderr line unchanged, so the `served` half fails on its own. Under the old fixture that mutation was invisible.

## FD2-7: the WS handshake refusal on the wire

- New `test_a_ws_handshake_without_the_token_is_refused_with_http_403` in `test_fixdiff2_daemon.py`: a real `daemon.Server` (uvicorn) on a free port, token configured, `server._LOOPBACK_CLIENTS` monkeypatched to empty so a 127.0.0.1 client is treated as the network client the guard is written for. `websockets.connect` on `/ws` with no token and with a wrong token both raise `InvalidStatus` with `response.status_code == 403`; the same handshake carrying the token is the positive control, asserting 101.
- Observed status is 403, so SPEC 3.1 stands as written; nothing in SPEC was changed for this.
- `test_hardening.py:741-747`: comment corrected. It now says 1008 is what the TestClient sees because `_deny` sends a pre-accept ASGI close, which uvicorn puts on the wire as HTTP 403, and that the lockout refusal is a different method (`_deny_rate_limited`, close 1013 there). `_deny` and the CLI were left as they are.
- No revert leg: the test is new and pins existing behaviour. Its own negative half has the 101 positive control in the same test, and the 403 was observed, not assumed.

## Checks run

`uv run python -m ruff check .` clean.
`test_fixdiff2_daemon.py`, `test_rulings_daemon_startlog.py`, `test_rulings_daemon_config.py`, `test_hardening.py`, `test_rulings_daemon_plot.py`, `test_session_bundle.py`, `test_decode_per_port.py`: 150 passed.
`test_plot.py`, `test_prerelease_daemon_core_plot.py`, `test_daemon_r2026_09_12_bundle.py`, `test_timeline.py`, `test_capture_lock.py`, `test_pidfile.py`, `test_daemon_startup.py`: 93 passed.
The full suite was not run: other batches are mid-edit.

## CHANGELOG lines

- Fixed: `GET /plot/channels` no longer re-reads a detached board's stored `!pd` definitions on every request; the learned set is kept until the capture is replaced or the alias is attached again.
- Fixed: a session bundle holding two stored ports whose names differ only in characters the member naming replaces (`a/b` and `a_b`) now writes one member per board (`plot_a_b-2_3.csv`) instead of the same name twice, which lost the first board's rows and listed it twice in `manifest.json`.
- Fixed: the WebSocket handshake refusal is documented as the HTTP 403 it is on the wire (a browser can only report it as close 1006), replacing the close 1008/1013 wording (SPEC 3.1).

The third line is the one the chrome leg found missing for the round's own SPEC 3.1 rewrite; the first two cover this batch.

## Not done

- FD2-5 (`child_env` is a no-op on Windows): owner question, untouched.
- Nothing needed a file outside the batch's list. The CLI's `close_1008` branch (`cli.py:2286-2293`) is FD2-7's "keep or retire deliberately" half and belongs to the CLI batch or the owner; it is harmless either way, since the close code can still arrive from the post-accept refusals.

## The two questions

1. **Least confident, rechecked.** The attach invalidation of the FD2-3 cache: I claimed `POST /ports` is the only transition into the manager that can be followed by new stored definitions. Rechecked by enumerating every `.attach(` call in `server.py` rather than reasoning about it: three, of which one is the startup lifespan (`server.py:460`, before any request, so the cache is empty) and one is `/ports/{alias}/reconnect`, which returns 400 unless the port is already in the manager and therefore cannot be the first entry. The third is the one that pops. The test drives the real failure (stale labels after attach-store-detach), not the pop itself, so it would also catch a fourth path if one appeared through `PortManager`.
   Second: that the 403 in FD2-7 is uvicorn's doing and not the test client's. It was observed against a real listener on a free port with `websockets`, and the 101 in the same test is the control.
   Not confident and not driveable here: the Windows half of FD2-1. The regex accepts both renderings and the mutation proves both assertions can fail, but no `[WinError` string was ever produced on this host.
2. **What should have been checked that nobody thought about.** The cache in FD2-3 is per app object, and the tests reach it through one app that lives for the test. Nothing in the suite covers a long-lived daemon whose detached board is later purged *partially* (a `before_ts` sweep that removes the `!pd` rows but not the plot points, leaving the capture id intact): the cached meta then outlives the rows it was learned from, and a fresh daemon would answer with null fields where the running one answers with the old definitions. It is a narrower window than the one before the fix (which re-scanned and so would report the nulls), and it does not lose data, but it is a real difference between two daemons over one capture that no test states either way.
   Also unlooked-at across the round: every other per-board artefact keyed by a sanitised name. FD2-4 fixed the bundle; `export_filename`'s `session` sanitisation (SPEC line 870) has exactly the same shape, and two sessions differing only outside `[A-Za-z0-9._-]` produce one download name. That one is a user-facing filename rather than a zip member, so nothing is silently lost, but it is the same normalisation without a uniqueness check.
