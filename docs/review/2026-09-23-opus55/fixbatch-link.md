# Fix batch: link

HEAD: `b994076c88c875ad43c30789cda6039d147b7abe` (uncommitted edits on top).

Revert-verify copy and script: `~/tt-data/mcuscope-2026-09-24/fixbatch-link/` (`mutate.py`, results in `mutate.log`).
The copy was confirmed to be the tree imported (`mcuscope.__file__` printed from the copy).

## F1. Windows console close gets the graceful stop

- `host/mcuscope/daemon.py:392` new `_hold_console_close()`, called at `:474` right after `_release_pid_on_terminating_signal(pid_path)`.
  - Installs `_stdio.install_console_ctrl_handler()` when `_stdio._ctrl_handler_ref is None and _stdio.have_console()`.
- `install_console_ctrl_handler` is NOT safe to call twice: the second call overwrites `_ctrl_handler_ref`, so the first thunk (still registered with Windows) can be garbage-collected.
  - Windows calls it for any event the newer handler returns False on (CTRL_LOGOFF 5, CTRL_SHUTDOWN 6): a call into freed memory.
  - Worked around in `daemon.py` by the `_ctrl_handler_ref is None` guard; `_stdio.py` not edited. Proposed `_stdio.py` edit under "Doubts".
- The `have_console()` guard keeps `mcu daemon start` (DETACHED_PROCESS, no console) untouched: the install would clear its inherited ignore-Ctrl-C flag for no gain.
- Test: `host/tests/test_link_fixdiff_console.py` (3 cases) drives `daemon.main` with `_serve` stubbed and the installer replaced:
  - console present, no handler yet: `["install", "serve"]`;
  - handler already installed by a late attach: not installed again;
  - no console: not installed.
- Revert: dropping the call, the ref guard, or the console guard each fails one case (KILLED x3).

## F2. Reconnect checks identity, not existence

- `host/mcuscope/serial_link.py:1362`: `attach(..., require_existing: bool)` replaced by `replaces: SerialPort | None = None` (None = no check, what `POST /ports` uses).
  - `:1370` records `was_held = replaces.held` at entry (no await between the route's read and this).
  - `:1401-1408`, under the lock:
    - alias gone: `PortError("no such port: <alias>")` (unchanged text);
    - alias now a different object: `PortError("port <alias> was re-attached during the reconnect")`;
    - port held since entry: `PortError("port <alias> was disconnected during the reconnect")`.
- `hold()` has no race of its own (lock held throughout, no await before the route's `get`); its race was being undone by a reconnect, which the held check above covers.
  - A reconnect of a port already held before it began still resumes it (that is what reconnect is for).
- `host/mcuscope/server.py:1167-1169` (`reconnect_port` only): `replaces=pt`.
- `host/tests/test_serial_link_attach.py`: switched to `replaces=`.
- Tests in `host/tests/test_link_fixdiff_races.py`:
  - `test_a_reconnect_does_not_undo_a_reattach_made_during_its_prime`;
  - `test_a_reconnect_does_not_undo_a_disconnect_made_during_its_prime`;
  - `test_a_reconnect_resumes_a_port_held_before_it_began` (positive control for the held check).
- Revert (all KILLED):
  - identity check dropped (the old code's behaviour for a re-attach): re-attach test fails;
  - held check dropped (the old code's behaviour for a disconnect): disconnect test fails;
  - `was_held` ignored: resume control fails;
  - missing-alias branch dropped: `test_serial_link_attach.py` fails on the message;
  - `replaces=pt` removed from the route: `test_server_lifespan.py::test_a_detach_during_a_reconnect_is_not_undone` fails.

## F4. Mid-batch cancel test

- `host/tests/test_serial_link_rx_framing.py:168`: `_StalledStore(take=k)` accepts the first k `submit_line_nowait` calls, then is full for good.
- The test (`n, k = 500, 10`) asserts `store.taken == k`, `rx_dropped == n - k`, and the row `dropped 490 received lines not yet stored at detach`.
- Revert: `batch[i:]` to `batch` fails it (KILLED).

## Nits

- `serial_link.py:1-9` docstring: writes, the link close and `_write_health` are under `_write_lock`; the reader publishes `_link` and `stop()` reads it without the lock.
- Stop cause passed in: `SerialPort.stop(cause="detach")` (`serial_link.py:408`), row `... at {cause}`.
  - `hold()` passes `"disconnect"`; an attach replacing an alias passes `"re-attach"` via `_detach_locked(alias, cause)`; detach and shutdown keep `"detach"`.
  - Test: parametrized `test_the_dropped_partial_row_names_what_stopped_the_port` in `test_link_fixdiff_races.py` (disconnect, re-attach, detach).
  - Revert: each of the four plumbing points reverted fails it (KILLED x4).
- `host/tests/test_config_api.py:440`: `PortManager.resolve` removed from the docstring.

## Test runs (shared tree, one file at a time)

- New: `test_link_fixdiff_console.py` 3, `test_link_fixdiff_races.py` 6.
- Existing: serial_link_attach 1, serial_link_rx_framing 9, reconnect 51 (1 skip), daemon_hangup_and_race_reports 5, config_api 30, port_health 27, regressions 83, serial_link_tx 6, serial_link_rx_tokens 9, e2e 39, stdio 13, server_lifespan 12: all pass.
- `uv run python -m ruff check .`: clean.

## SPEC wording

- SPEC 3.4, `POST /ports/{alias}/reconnect` (line 707), replace the second sentence with:
  - "Returns `{"port": {...}}` like attach; 400 for an unknown alias, and 400 when the alias was detached, re-attached or disconnected while the reconnect was under way (none of those is undone)."
- SPEC 3.2 item 7 (line 466), add after the SIGHUP sentence:
  - "On Windows, closing the console window a daemon runs in gets the same graceful shutdown, within the roughly 5 s Windows allows."

## Proposed CHANGELOG lines

- Windows: closing the console window of a foreground `mcuscoped` now stops it gracefully (daemon stop row, session closed, pid record removed) instead of killing it.
- `mcu port reconnect` no longer undoes a re-attach or a disconnect of the same port made while it was starting; it answers 400 instead.
- The row for lines dropped when a port stops now says whether it was a detach, a disconnect or a re-attach.

## ARCHITECTURE (not edited, not mine)

- `docs/ARCHITECTURE.md:76-77` (daemon.py): startup order gains "hold the console close (Windows)" after "install the signal handlers"; add "closing the console window on Windows arrives as SIGINT via the ctrl handler".

## Not done

- `_stdio.py` idempotence fix (owned by another batch); proposed below.
- No route-level test for the re-attach and disconnect races (the manager-level tests cover the check; the route's `replaces=pt` is pinned by the existing lifespan detach test).

## Needs Windows

- From cmd and from PowerShell, run `mcuscoped -c <throwaway.toml>` in the foreground and close the window. Expect:
  - the capture's sys rows end `daemon stop`;
  - the session is closed;
  - the pid record is gone.
- Same with a late-attach start (pythonw-based venv) to confirm the handler is not installed twice and behaviour is unchanged.
- `start /b mcuscoped ...` in cmd, then Ctrl-C in that window: see the first doubt.
- `mcu daemon start` then `mcu daemon stop`: unchanged (no console, so no install).

## Doubts

- `start /b` behaviour change (owner should pick). `start /b` launches with Ctrl-C ignored. `install_console_ctrl_handler` calls `SetConsoleCtrlHandler(None, False)`, which clears that flag, so Ctrl-C in that cmd window now stops the daemon. The late-attach path already did this.
  - Recommended: the `_stdio.py` edit below, with the daemon passing `keep_ctrl_c_ignored=True`, so only the close hold is added.
- Proposed `_stdio.py` edit, `install_console_ctrl_handler` (`_stdio.py:54`):
  - signature `def install_console_ctrl_handler(keep_ctrl_c_ignored: bool = False) -> bool:`;
  - after the `sys.platform` check: `if _ctrl_handler_ref is not None: return True` (idempotent; the daemon's private-attribute guard can then go);
  - `if not keep_ctrl_c_ignored: k32.SetConsoleCtrlHandler(None, False)` in place of the unconditional call.
- With the handler installed, CTRL_BREAK in the console now arrives as SIGINT, not SIGBREAK. Both are graceful; the exit code differs.
- The handler holds CTRL_CLOSE for 4.5 s, but `GRACEFUL_SHUTDOWN_S` is 5 s plus the lifespan finaliser. A close during a long `/wait` can still be killed before the stop row lands.
