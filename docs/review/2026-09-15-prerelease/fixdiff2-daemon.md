# Fix-round diff review: daemon Python (`9ad910c..HEAD` = 5489d6e)

Files: `host/mcuscope/{server,store,daemon,serial_link,config,pidfile,_stdio,__init__}.py`, `host/tests/*` less the CLI and web UI files, plus the `SPEC.md` / `CHANGELOG.md` paragraphs that describe daemon behaviour.

Scratch probes: `~/tt-data/prerelease-2026-09-15/fixdiff2-daemon/` (`test_fixdiff2_pid.py`, `test_fixdiff2_plotchannels.py`, `test_fixdiff2_plan.py`).
Run from `host/`: `uv run python -m pytest -s -q -p no:cacheprovider --rootdir=. ~/tt-data/prerelease-2026-09-15/fixdiff2-daemon/<file>`; each probe prints its observation to stderr.
No daemon was started, port 8558 was never touched; every probe is a TestClient app or a bare `Store` on a scratch file DB.

Totals: 0 HIGH, 1 MED, 5 LOW.

Ruled out (kept as negative results):

- `_settled_plot_summary`'s `or self._plot_lock.locked()` has no gap: `asyncio.Lock.acquire()` sets `_locked` synchronously and there is no await between the check and the `async with`, so a reader can never slip past a rebuild that has started.
- `plot_streams`'s new `JOIN lines` is not a class 20 regression: the plan still drives from `plot_points` on `idx_plot_line`, then one rowid lookup into `lines` (driven, `test_fixdiff2_plan.py`).
- `_write_doc`'s switch to `write_bytes` keeps the Windows CRLF fix and makes the returned revision the hash of the bytes actually on disk; `config_revision` is content-based, so Windows' coarse file timestamps cannot make two different files share a revision.
- `_note` and `cli_output.err_write` both catch `OSError` alone, so `_stdio.py:352`'s "the same guard" claim holds.
- The `/ws` shed-row gap the CHANGELOG promises does happen: the pump truncates at the sentinel, then calls `store.take_dropped(q)` and inserts `{"gap": n}` before the last frame (`server.py:2010-2023`).
- Class 66 on `handle_exit`: it schedules `stop_subscribers` and touches no loop state.
- `_serve`'s `SystemExit` swallow cannot rewrite a clean exit as a failure: `server.started` stays true through uvicorn's shutdown, pinned by `test_a_start_that_served_leaves_the_started_log`.
- `_warn_sink` does not leak across requests: `read_config` sets it on every call that can warn, and `asyncio.to_thread` runs in a copied context, so `app.state.config_warnings` cannot pick up a later read's warnings.
- `pidfile.claim` cannot hang on a FIFO: it opens with `O_CREAT|O_EXCL`, so it never opens an existing path.
- `/cmd`'s new 503 maps to exit 3 route-independently (`cli_client.fail` matches `SHUTDOWN_PREFIX` on the body).
- `learn_stored_plot_defs(store, None, ...)` is unreachable: no plot point is stored against a portless line.

## Findings

### FD2-1 MED: the bind-failure test asserts `[Errno`, which is `[WinError` on Windows

- Where: `host/tests/test_rulings_daemon_startlog.py:64` (`assert reason.startswith("reason: [Errno")`).
- Defect: the reason is `str(exc)` of the bind `OSError`, carried from uvicorn (`Server.startup`: `logger.error(exc)`) through `_FirstError.emit`'s `record.getMessage()` (`daemon.py:294`). CPython renders an `OSError` that carries `winerror` as `[WinError %d] %s`, and the socket layer sets `winerror` on every Windows socket error.
- Failure: on Windows 10/11 the startup log's second line is `reason: [WinError 10048] Only one usage of each socket address (protocol/network address/port) is normally permitted`, so the assertion is false and the test fails on the Windows leg. The product behaviour is correct; only the assertion is Linux-shaped.
- REASONED (no Windows host here): uvicorn 0.52.0 `server.py` `startup()` lines 75-78, and `OSError.__str__`'s winerror branch. The bind itself does fail on Windows, because uvicorn's default `loop.create_server(host=, port=)` sets neither `SO_REUSEADDR` (asyncio does that on POSIX only) nor `SO_EXCLUSIVEADDRUSE`, so the test cannot instead hang.
- Class: 14 (a fix, or here an assertion, gated on one platform's rendering).
- Fix: assert `re.match(r"reason: \[(Errno|WinError) \d+\]", reason)`, or drop the bracket form and assert the reason names the port.

### FD2-2 LOW: "refused before the pid claim" is asserted at a point that never holds a pid record

- Where: `host/tests/test_rulings_daemon_config.py:275` and `host/tests/test_rulings_daemon_startlog.py:101`, both `assert not list(<data dir>.glob("*.pid"))`.
- Defect: `daemon.main` releases the record in its `finally` (`daemon.py:484-486`), so no `*.pid` survives `main()` on any path. The assertion passes identically when the claim did happen, so it cannot fail for the reason its message gives (class 78: the observation point never receives the thing).
- Failure: a served start over a config that exists leaves the same empty directory. With the "named config must exist" guard reverted, `main()` would claim, serve and release, and both assertions would still pass.
- DRIVEN: `test_fixdiff2_pid.py::test_probe_a_successful_start_also_leaves_no_pid_record` (`pid files after main(): []` on the served path), with `test_probe_the_claim_does_happen_on_the_served_path` as the positive control (`claimed: [.../mcuscoped-127.0.0.1-<port>.pid]` on the served path, `[]` on the refused one).
- Class: 78.
- Fix: spy on `pidfile.claim` as the probe does, or assert on the capture lock file, which the refusal also precedes and which is not removed on the refused path.

### FD2-3 LOW: `/plot/channels` re-learns every unattached port's stored `!pd` rows on every request

- Where: `server.py:1778-1785`. A row whose port is not in `plot_channel_meta_by_port()` builds a fresh `PlotDecoder` and calls `learn_stored_plot_defs`, which runs a regex-matched scan over up to `PLOT_DEF_LOOKBACK` (20000) ids. The result is cached in the request-local `by_port` only.
- Defect: an attached port primes its decoder once, at attach (`prime_plot_defs`). A port with no decoder pays the same scan on every call, for as long as its rows are in the capture. The web UI's seed makes `1 + len(ports)` calls per page load (`api.js:314` and `:322`), and re-seeds on a capture reset and clear-all.
- Failure: 20012 rows, 5 detached ports. Unfiltered `/plot/channels` 28.1 ms with 1 rescan; the identical second call does the same scan again (no cache); the per-port leg of one seed is 120.8 ms for 5 calls, 5 rescans. Off the loop, so it is latency, not a stall.
- DRIVEN: `test_fixdiff2_plotchannels.py::test_probe_detached_ports_are_rescanned_per_request`.
- Class: 1 and 20's neighbourhood; introduced by C-3's own fix.
- Fix: memoise the learned meta per alias on the `PortManager`, invalidated on `store.capture_id` or on attach; a detached board's stored definitions do not change.

### FD2-4 LOW: a sanitised bundle member name can collide, and the zip then carries the name twice

- Where: `server.py:1474` (`stem = f"plot_{_FILENAME_UNSAFE.sub('_', port)}_"`), written through `zipfile.ZipFile.open(arcname, "w")` at `server.py:1520`.
- Defect: `_FILENAME_UNSAFE` maps every character outside `[A-Za-z0-9._-]` to `_`, so two stored ports differing only in such a character share a stem. `test_bundle_member_names_sanitise_the_port` establishes that stored rows are not held to the alias grammar (an older daemon, a merged capture), which is exactly the case that makes this reachable.
- Failure: a capture holding both `a/b` and `a_b` with stream 3 writes `plot_a_b_3.csv` twice. `zipfile` warns `Duplicate name`, `namelist()` returns it twice (so `manifest.json` `files` lists it twice, breaking SPEC 3.4's "listing exactly the zip's entries"), and a reader gets only the second board's rows.
- DRIVEN (the `zipfile` half: two `writestr` calls under one name give a duplicate warning, a two-entry `namelist()` and the second body on read). REASONED (reaching it: two such ports in one capture).
- Class: 57's neighbourhood (a per-board artefact keyed by a name that is not unique after normalisation).
- Fix: disambiguate a stem already used in this bundle (append `-2`), and keep the manifest built from the disambiguated names.

### FD2-5 LOW: `child_env()` is a no-op on Windows, so a child there is not isolated from the user's dirs

- Where: `host/tests/support.py:48-60`. It sets `XDG_DATA_HOME`, `XDG_CONFIG_HOME` and `XDG_CACHE_HOME` only, and says so.
- Defect: platformdirs 4.11.0 picks `get_win_folder_via_ctypes` whenever `ctypes` imports (`platformdirs/windows.py` `_pick_get_win_folder`), so on CPython for Windows the XDG variables are never consulted; they are the POSIX class's input alone. A child `mcu` or `mcuscoped` on Windows therefore resolves the user's real data, config and cache dirs.
- Failure, per test in the diff whose assertion depends on the child honouring the redirect. **None goes vacuous**: every one is already skipped on Windows.
  - `test_cli.py` `test_daemon_start_timeout_does_not_orphan_the_child`, `test_daemon_start_pid_file_is_keyed_by_host_port`, `test_daemon_stop_no_pidfile_exit1`, `test_daemon_stop_corrupt_pidfile_exit1`, `test_daemon_stop_keeps_the_record_of_a_pid_that_is_still_running`, `test_daemon_stop_falls_back_to_the_api_when_no_record_exists`, `test_daemon_stop_asks_status_before_giving_up_on_a_corrupt_record`: `@_PIDDIR_ENV_SKIP` (`test_cli.py:792`, `os.name == "nt"`).
  - `test_cli_ux.py::test_restart_of_a_running_daemon_swaps_the_pid` and `test_review_r2_cli.py::test_daemon_start_reports_an_unusable_data_dir_without_spawning`: the same marker.
  - `test_pidfile.py::test_daemon_releases_pid_file_on_sigterm`: `skipif sys.platform == "win32"` (line 134).
  - `test_prerelease_cli_fixes.py::test_a_usage_error_with_stderr_closed_keeps_exit_1`: `skipif sys.platform == "win32"`.
  - The crash-log absence tests (`test_rulings_cli_closed_pipe.py:77`, `test_sweep_cli_closed_output.py:128,167`) do not use `child_env` at all: they patch `platformdirs.user_data_dir` inside the child and each has a positive control, so they are correct on both platforms.
  - Every other `child_env` call (`test_scaffold.py:97`, `test_sim_tcp.py:286`, `test_sim_pty.py:51,97`, `test_break.py:170`, `test_cli.py:65,81,522,581,1297,2414,2537,2545,2555,2708`) asserts only on the child's own output, so nothing is vacuous there either.
- What is actually lost on Windows is the isolation the comment promises: those children read the user's real config and update cache, and a child that crashes writes `mcu-crash.log` into the user's real data dir, beside their capture. That is the pollution `child_env` was added to stop, and it is unguarded.
- DRIVEN (the skip audit: every `child_env` caller enumerated from the source and its decorators read). REASONED (the platformdirs resolution order, read from the installed 4.11.0 source).
- Class: 33 (a test running the real entry point inherits the user's real environment), unclosed on one platform.
- Fix, **owner question** (it adds product code for a test need): have the three resolvers go through one helper that honours `MCUSCOPE_DATA_DIR` / `MCUSCOPE_CONFIG_DIR` / `MCUSCOPE_CACHE_DIR` before falling back to platformdirs, and set those in `child_env` beside the XDG ones. It is five call sites (`config.py:141,149`, `pidfile.py:61`, `_stdio.py:293`, `update_check.py:97`); no test-side trick works, because ctypes wins over every environment variable.

### FD2-6 LOW: the flag-beats-variable test cannot observe "nothing was started" on its second run

- Where: `host/tests/test_rulings_daemon_config.py:293`, `assert run_main("-c", str(missing)) == (1, True)` with the comment "served stays True from the first run".
- Defect: `run_main` returns `bool(served)` over a list the first, successful run already appended to, so the second half of the tuple is a constant. The refusal is still pinned by the exit code and the stderr line, which are unique to that path, so this is a weak line rather than a vacuous test.
- REASONED (read from the fixture; the comment states the same thing).
- Class: 78's shape, in a test that is otherwise fine.
- Fix: `served.clear()` between the two runs, or return `len(served)` and assert it did not grow.

## Hunks read

Source: `server.py` 28, `config.py` 19, `store.py` 5, `daemon.py` 5, `serial_link.py` 3, `_stdio.py` 3, `pidfile.py` 1, `__init__.py` 1.
Tests: `test_config_api.py` 6, `test_daemon_startup.py` 5, `test_protocol.py` 4, `test_regressions.py` 4, `test_review_r2_config.py` 4, `test_scaffold.py` 3, `test_session_bundle.py` 3, `test_sim_pty.py` 3, `test_break.py` 2, `test_capture_lock.py` 2, `test_decode_per_port.py` 2, `test_review_r2_server.py` 2, `test_sim_tcp.py` 2, and 1 each in `conftest.py`, `support.py`, `test_daemon_r2026_09_12_bundle.py`, `test_eol.py`, `test_hardening.py`, `test_pidfile.py`, `test_plot.py`, `test_plotjuggler.py`, `test_prerelease_daemon_core_plot.py`, `test_timeline.py`, and the three new files `test_rulings_daemon_config.py` (325 lines), `test_rulings_daemon_plot.py` (209), `test_rulings_daemon_startlog.py` (102), `test_sweep_daemon_classes.py` (180), each read whole.
Docs: every `SPEC.md` hunk (26) and `CHANGELOG.md` hunk (9) was read; the web UI paragraphs of both were read for contract agreement but are the web UI legs' to rule on.

Not read: `cli.py`, `cli_client.py`, `cli_output.py`, `webui/*`, and the CLI and web UI test files, per the leg split.
The bundle's `manifest.json` builder past `server.py:1520` was read only far enough to confirm it lists `zf.namelist()`.

Contract check: every daemon behaviour change in the diff has both a SPEC paragraph and a CHANGELOG line, and they agree with the code. Checked one by one: the `revision` / 409 protocol (SPEC 3.3.1, and "without `revision` there is no check" matches `_read_doc`'s `revision is not None` guard), `config_warnings` on `/status` ("does not follow later edits" matches `app.state.config_warnings` being fixed at startup), the named-config refusal (SPEC 3.3 and the section 4 `mcu daemon` row), `/cmd`'s shutdown 503 with its exact message, the negative deadband refusal including "`-0` is zero", the per-port bundle members with the sanitisation rule, `/plot/channels` taking a detached board's own stored definitions with null fields as the fallback, the failed-start startup log, and the WS 403 handshake refusal replacing close 1008/1013 for auth.

## The two questions

1. **Least confident, and rechecked.**
   - FD2-1 is the one I cannot drive: there is no Windows host here, so the `[WinError` rendering is read from CPython's `OSError.__str__` and uvicorn's `logger.error(exc)`, not observed. I rechecked the adjacent assumption instead, since a wrong one there would turn a failing test into a hanging one: uvicorn's default bind sets neither `SO_REUSEADDR` nor `SO_EXCLUSIVEADDRUSE` on Windows, so the bind does refuse and the test fails rather than serving forever. `_port_conflict`'s probe does not cover it, because the test calls `_serve` directly.
   - My first reading of FD2-3 claimed one rescan per detached port per unfiltered request. The probe refuted it: the unfiltered list collapses a shared name to the port with the newest sample, so the unfiltered call rescans once, and the cost lands on the per-port leg of the seed instead. The reported numbers are the corrected ones.
   - FD2-4's daemon path is reasoned; only the `zipfile` behaviour was driven.
   - Nothing was run on Windows, and no browser or real daemon was involved.
2. **What should have been checked that nobody thought about.**
   - The *fallback* half of every new mechanical enumeration. The round added several ("every PUT /config route", "every body taking eol", "every int() in protocol.py", "every declared console script"), and each is derived correctly, but each also carries a hand-kept exemption set (`_INT_EXEMPT`, `_FIRMWARE_ONLY_REJECTED`) that is exactly the class 75 shape one level down. `_FIRMWARE_ONLY_REJECTED` at least asserts it is not stale; `_INT_EXEMPT` is compared by equality, so it is fine, but nothing asserts the exemptions are still *reachable*.
   - The cost of a correctness fix on the path that was already the cheap one. C-3 replaced a dictionary lookup with a store scan and no leg measured it; FD2-3 is that gap.
   - Which of the round's new assertions can fail at all. Three of the new negative assertions sit at observation points that are empty on every path (FD2-2, FD2-6); the class 78 sweep was run over the pre-existing tests, not over the tests the fix batches themselves wrote.
