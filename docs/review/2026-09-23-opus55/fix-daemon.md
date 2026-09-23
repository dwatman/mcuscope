# Fix batch: daemon

Files changed: `host/mcuscope/daemon.py`, `host/mcuscope/config.py`, `docs/SPEC.md` (own sections).
New tests: `host/tests/test_daemon_token_exposure.py`, `host/tests/test_config_loader_host_device.py`, `host/tests/test_daemon_hangup_and_race_reports.py`.
Revert-verify script and log: `~/tt-data/mcuscope-2026-09-23/fix-daemon/revert.py`, `revert.log`. All 17 mutants were caught.

## LIFECYCLE-5: a start-race loser overwrote the running daemon's startup log

- Change: `daemon.py:328` `_report_key()`, called at `daemon.py:459` right after `pidfile.claim`.
  - If another live process holds the host:port pid record, this process keys its startup and crash logs `host-port-<pid>`.
  - A record naming this process's parent (the Windows launcher shim that `mcu daemon start` records) keeps the shared name.
  - So does a record naming nothing live, or one that cannot be read.
- Tests (`test_daemon_hangup_and_race_reports.py`):
  - `test_a_start_that_loses_the_bind_race_leaves_the_winners_log`: in-process `main()` against a live foreign record plus a listener on the port. It asserts the winner's log is byte-identical and the loser's pid-keyed log says `failed to start, pid <ours>, exit 3`, which is the positive control.
  - `test_report_key_follows_the_record_holder`: no record, record held, foreign live, parent, dead pid.
  - `test_report_key_survives_an_unusable_data_dir`.
- Revert-verify: removing the re-key call, the `pid_path` early return, `getppid`, the `pid_running` gate, or the `OSError` guard each fails a named test (L5-* in the log).
- Real race: 4 trials of two `mcuscoped` on 127.0.0.1:18861 with different `db_path`. Each time the record holder won the bind, the shared log said `started, pid <winner>`, and the loser's `failed to start` went to `...-18861-<loser pid>-startup.log`.
- `.err` claim (CLI batch's file): confirmed by reading `cli.py:2429-2431`. `_start_daemon` opens `_stderr_log_path(pid_path)` (keyed by host:port only) with `"wb"` before the spawn. A second `mcu daemon start` on the same host:port therefore truncates the file the first daemon's stderr is writing to (non-`O_APPEND`, so the winner's next write lands at its old offset after a hole). See "Not done".

## LIFECYCLE-6: SIGHUP skipped graceful shutdown

- Change: `daemon.py:349-388`, `_release_pid_on_terminating_signal`.
  - On POSIX, a SIGHUP left at `SIG_DFL` gets a handler that raises SIGTERM. That is uvicorn's graceful path, followed by the replay into the pid-release handler.
  - An ignored SIGHUP (`nohup`) is left ignored. The handler only raises a signal, so class 66 holds.
- Tests: `test_sighup_shuts_down_gracefully` spawns a daemon and sends SIGHUP. It checks: exit `-SIGTERM`, pid record gone, last daemon sys row `daemon stop` (with `daemon start` present as the query's positive control).
  - `test_an_ignored_sighup_stays_ignored` runs the daemon under `SIG_IGN` and checks it is still serving 2 s after the SIGHUP.
- Revert-verify: L6-hup (handler not installed) fails the first test. L6-ign (install over `SIG_IGN`) fails the second.

## HEALTH-4: `server.host = ""` bound every interface

- Change: `config.py:67` `check_host()` returns the stripped host, or raises ValueError when the host is empty or has whitespace or a non-printable character inside.
  - Loader `config.py:391-398`: warns `config: [server] host must be a host name or address, not ''; using '127.0.0.1'` (into `config_warnings`) and keeps 127.0.0.1.
  - `--host` `daemon.py:103-108`: the same check, refused as `--host must be ...` (message kept, so `test_review_r2_config.test_an_empty_host_override_is_refused` passes unedited).
- Tests: `test_config_loader_host_device.py::test_an_empty_host_keeps_loopback_and_says_so` and `::test_a_wildcard_host_is_still_a_host` (`" 0.0.0.0 "` loads as `0.0.0.0` with no warning).
  - `test_daemon_token_exposure.py::test_a_host_flag_that_is_no_address_is_refused` (6 inputs incl. `a b`, NUL, DEL) and `::test_a_host_flag_is_applied_stripped`.
- Revert-verify: H4-loader, H4-printable, H4-strip, H4-flag all caught.

## HEALTH-5: the loader accepted a device PUT /config/ports refuses

- Change: `config.py:458-464` runs `link.validate_device` (the same function `serial_link.validate_device` wraps for the API) on each port's device. A refused entry is skipped with `config: port 'spy' device scheme not allowed: spy://; skipping it`.
- Tests: `test_config_loader_host_device.py::test_a_device_the_api_refuses_is_not_loaded` (scheme and `?` options) and `::test_the_loaded_ports_save_back` (GET /config then PUT /config/ports of that list answers 200).
- Revert-verify: H5-device caught.

## HEALTH-11: token and exposure handling untested

- Probe tests e01..e04 are moved into `test_daemon_token_exposure.py`, widened:
  - e01 became the parametrized host refusal above.
  - e04 also asserts a whitespace-only `MCUSCOPED_TOKEN` means no token.
- Revert-verify: E02 (short-token warning), E03 (tokenless warning), E04 (env token applied), E04-strip, E-flagwins (flag over env) each caught. E01 is H4-flag.

## HEALTH-20 SRC-6 (config half): NOT DONE, see "Not done"

## Existing tests edited

None.

## SPEC edits

- 3.1, startup log bullet: a start whose pid record names another live process keys its startup and crash logs with its own pid.
- 3.2 item 7 (pid record): SIGHUP on POSIX is handled as SIGTERM; an ignored SIGHUP stays ignored.
- 3.3 loader bounds: `server.host` rule, warn-and-127.0.0.1, shared with `--host` and `PUT /config/server`.
- 3.3 skipped `[[ports]]` entries: a `device` the network API refuses.

## Changelog

- A daemon that loses a start race to another on the same address writes its startup log under its own pid, not over the running daemon's.
- Closing the terminal of a foreground `mcuscoped` (SIGHUP) now shuts it down cleanly: `daemon stop` row, session closed, pid record removed.
- `server.host = ""` (or one with spaces or control characters) in config.toml warns and binds 127.0.0.1 instead of every interface; `--host` refuses the same values.
- A config port whose device the API refuses (`spy://`, `?` options) is skipped with a warning, so the settings dialog can save ports again.

## Not done

- SRC-6 config_warnings: not doable in my files without a second copy of the unicast rule (class 19). The runtime refusal happens in the server lifespan, and `pjstream._resolve` holds the only unicast rule.
  - server.py (server batch), lifespan at about `server.py:412-417`: in the `except (ValueError, OSError)` branch, also record the message where GET /status reads it. For example, build `app.state.config_warnings` before the PlotJuggler block and append `f"plotjuggler: cannot enable for {dest!r}: {exc}"` to it. Today the copy at `:423` runs after the enable, so an append to `config_warnings` there is lost.
  - pjstream.py (firmware batch), optional: the src-py.md suggestion of a no-lookup non-unicast check for address literals in `parse_dest`. The loader's existing `parse_dest` call (`config.py:424`) would then warn into `config_warnings` by itself, with no change in config.py. It falls back to the default dest, as for any bad dest.
- `PUT /config/server` (server batch, `server.py:1136-1138`): replace the inline check with `from .config import check_host`, then `try: host = check_host(body.host) except ValueError as exc: return _bad_request(f"host {exc}")`.
  - The inline rule today accepts DEL and other non-printables that `check_host` refuses. SPEC 3.3 now states the one shared check.
- CLI `.err` truncation (CLI batch, `cli.py:2429-2431`): a second `daemon start` on the same host:port truncates the running daemon's stderr log.
  - Fix option: open the log with `"ab"` and write a `--- start <iso time> pid-less spawn ---` separator line first. The winner's lines then survive, and `_stderr_tail` still shows the newest lines.
  - Alternative: key it by the spawned pid after `Popen` (rename a temp name to `...-<pid>.err`), at the cost of one file per start.
- ARCHITECTURE.md (tests batch), line 68 startup order: after "record the pid", add "key the startup and crash logs by record ownership". Replace "install the signal handler that releases that record" with "install the signal handlers (SIGTERM releases the record; SIGHUP is raised on as SIGTERM)".
- lockfile.py: no finding needed a change.

## Doubts

- LIFECYCLE-5 covers the order where the record holder wins the bind, which is what all four real trials produced.
  - In the other order the loser holds the record and writes the shared log, while the winner is unrecorded and logs under its own pid. That is the class 7 residual (the winner has no record either).
  - No live daemon's log is overwritten in either order, but the shared log then names a dead pid. Not tested: the order cannot be forced without patching `claim`.
- The parent-pid exemption rests on the Windows launcher shim being this process's direct parent, per `pidfile.py`'s docstring. I did not check it on Windows, and a record naming a bare `mcuscoped`'s parent shell after pid reuse would also get the shared name (harmless).
- Not run: the whole suite (per brief). `test_hardening.py::test_hoist_token_equals_form` failed during my run on `cli_argv.hoist_global_opts` missing: the CLI batch's in-flight edit, not these files.
- Ran: the new files plus `test_daemon_startup`, `test_review_r2_config`, `test_rulings_daemon_startlog`, `test_config_api`, `test_pidfile`, `test_stdio`, `test_rulings_daemon_config`, `test_daemon_r2026_09_12_config`, `test_config_bools`, `test_config_ports_eol`, `test_rulings_cli_config`, `test_fixdiff2_daemon`, `test_sweep_daemon_classes`, `test_plotjuggler`, `test_security`, `test_hardening` (228 + 106 tests); ruff clean on touched files.
