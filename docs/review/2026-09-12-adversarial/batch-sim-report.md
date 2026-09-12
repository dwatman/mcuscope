# Batch: sim narration, plot units, PlotJuggler startup line (HEAD c15b7c6)

## P8 - units and scales on the simulator's plot channels

- `host/mcuscope/sim.py` `Simulator._poll_plot`: `!pd 0 tri:s2*0.01:V ramp:u2*0.1:mA ftest:f4:degC`
  (was `tri:s2*0.01:V ramp:u2 ftest:f4`). Ad-hoc `!p` gains a third channel, `rpm` (2400 +- 300),
  three orders of magnitude above `sine`/`noisy`, so independent y scales stay demonstrated.
  Magnitudes: tri +-20 V, ramp 0..6553 mA, ftest +-1 degC, rpm ~2400.
- `host/tests/test_sim.py::test_plot_defs_declare_units_on_more_than_one_channel` (new): parses the
  sim's own `!pd` lines and asserts more than one channel carries a unit and more than one a scale.
- Old-string assertions moved: `host/tests/webui_js/api_plot_def_seed.test.mjs:29` (seed fixture).
  `host/tests/test_protocol.py:403` also contains the old text but is a grammar test with its own
  literal (it asserts `ramp` has no scale/unit by construction), not an assertion about the sim, so
  it was left alone.
- Revert check: reverted the two sim.py hunks, ran `-k units` -> FAILED
  `assert 1 = len(['tri'])`. Restored from /tmp/sim.py.mine.

## P7 - narrated simulator output

- `host/mcuscope/sim.py`: new `Simulator._poll_narration`, called from `poll_events` in place of the
  `sim alive n=<count>` beat. `SimState.alive_count` removed; `next_alive` becomes `next_reading`,
  plus `next_fault`, `fault_count`, `narr_state`; module constant `NARRATION_STATES`.
  - a line per step of the same 1 Hz state machine the enum stream runs (`state: IDLE -> ARMED`)
  - `vbat=..V iout=..A temp=..C` at 0.5 Hz
  - a `WARN ...` and an `ERR 3 timeout ...` line alternating on a 30 s beat (each ~1/min)
  - measured 1.5 lines/s, asserted < 2; no catch-up (a stalled poll emits one line, not a backlog)
- All narration is plain debug text. **Deviation worth the owner's eye:** P7 asks for an "`ERR`
  response line" so the web UI's `err` styling shows, but `terminal.js:92` only styles
  `chan === "resp" && /\bERR\b/`, i.e. it needs a real `<...` response line on the wire. That
  contradicts "no new wire syntax", and an unsolicited `<seq ...` would pop a pending seq in
  `serial_link._response_seq`. Implemented as a plain-text `ERR`-shaped debug line; the `err`
  colouring therefore still needs a typed command. Say the word if the wire-level version is wanted.
- Tests (`host/tests/test_sim.py`, new): `test_narration_is_readable_and_stays_off_the_wire`
  (70 s of synthetic time: transition, reading count within 0.5 Hz bounds, WARN, ERR, rate < 2/s,
  and every line classifies DEBUG with `parse_plot_adhoc` / `parse_plot_def` / `parse_can_event` /
  `parse_marker` all None and `parse_response` raising - the event parsers found in `protocol.py`);
  `test_narration_does_not_catch_up_after_a_stall`; `test_poll_events_narrates_with_no_command_typed`
  (drives the real `poll_events`, also asserts `sim alive` has not come back).
  Clocks are synthetic (`now` argument plus `state.start_ns` shifted back); nothing sleeps and no
  global clock is patched.
- Adapted existing tests that counted sim lines or named `sim alive`:
  `host/tests/test_sim.py::test_a_long_stall_re_anchors_the_periodic_schedules` (now asserts the
  narration emits exactly one owed reading, since it deliberately does not catch up) and
  `host/tests/test_sim_tcp.py:218` (allowed-line whitelist in the back-pressure test).
- Revert checks: (a) wiring only reverted (alive beat restored, method left in place) ->
  `test_poll_events_narrates_with_no_command_typed` FAILED "nothing narrated"; (b) whole sim.py
  restored from HEAD -> all three narration tests FAILED. Restored from /tmp/sim.py.mine both times.

## P15 - mcuscoped names the PlotJuggler destination

- `host/mcuscope/daemon.py` `main()`: one line beside `web UI: ...` when
  `config.plotjuggler.enabled`, printing `PlotJuggler: streaming plot points to <host:port>
  (--plotjuggler)`. argparse abbreviation untouched.
- `host/tests/test_daemon_startup.py::test_startup_names_the_plotjuggler_destination` (new, with a
  `_startup_output` helper that stubs `uvicorn.run` and reads capsys): silent without the flag
  (no "PlotJuggler", no "9870"), names `127.0.0.1:9870` and `--plotjuggler` when started with the
  abbreviation `--plot`, and names an explicit `--pj 10.0.0.5:9999` destination rather than the default.
- Revert check: daemon.py restored from HEAD -> FAILED
  `assert '127.0.0.1:9870' in 'web UI: http://127.0.0.1:56957/ui/\n'`. Restored from /tmp/daemon.py.mine.

## Docs

- `docs/SPEC.md` section 7: the `sim alive` bullet replaced by the narration bullet; the `--plot`
  bullets updated for the three ad-hoc channels and the unit/scale-bearing typed definition.
- `CHANGELOG.md`: `## [Unreleased]` section created with Changed (two sim lines) and Added (one
  daemon line).

## Gates

- `uv run python -m ruff check .` -> All checks passed.
- `uv run python -m pytest tests/test_sim.py tests/test_sim_tcp.py tests/test_daemon_startup.py
  tests/test_plot.py tests/test_webui_js.py -q` -> **89 passed, 1 failed**. The failure is
  `test_webui_js.py::test_webui_js_suite`, from another batch's in-flight edits to
  `webui/state.js`, `timewindow.js`, `statusbar.js`, `chrome.js`, `settings.js` (failing node cases:
  paused-window export, watermark, eol select, CSV escaping, pane filters). Not mine:
  `node --test tests/webui_js/api_plot_def_seed.test.mjs` (the only JS file I touched) passes alone,
  and the same suite passed alone before those files changed under me.
- `grep -rnP '[\x{2013}\x{2014}]' host/mcuscope/sim.py host/mcuscope/daemon.py docs/SPEC.md
  CHANGELOG.md` -> empty.

## Not done / breakage outside my files

- Nothing outside my file list is broken by these changes. A full-suite run mid-session showed 12
  failures; re-running that set with `sim.py` and `daemon.py` reverted to HEAD reproduced them
  independently of my change, and a like-for-like re-run with my files in place leaves only
  `tests/test_port_health.py::test_to_is_one_until_ts_on_the_query_itself` (fails with and without my
  change) plus the web UI JS suite above. Both belong to other batches.
- `host/tests/test_protocol.py:403` still contains the old `!pd` text as a grammar literal
  (deliberate, see P8 above).
