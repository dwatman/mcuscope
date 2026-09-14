# Leg F: test quality and revert verification (v0.4.0..fdd30a2)

Mutations ran only in exported copies under `~/tt-data/prerelease-2026-09-15/F/` (harness `mut.py`, spec `mutants.py`, raw `results.jsonl`).
Mutation table: `F-mutation-table.md` (155 mutants: 128 killed, 23 survived, 4 equivalent; every Python kill re-run against its credited test alone).

Full suite in the copy: 1608 passed, 1 skipped, 508 s. JS alone: 592 pass, 35 s.
Changed-line coverage (pytest-cov 7.1.0): 365 changed statements, 14 uncovered, 10 partial branches; coverage cannot see CLI tests that run `mcu` as a subprocess, so every survivor was re-run against the full suite.

## Findings

HIGH 0, MED 6, LOW 26.

- **F-1 MED** `tests/test_daemon_r2026_09_12_server.py:211`, `:241`: both shutdown-503 tests fire `handle_exit` after a bare `sleep(0.5)`, assuming the handler has subscribed.
  - DRIVEN: `sleep(0)` fails both 5 of 5. Class 21. Fix: poll `len(store._subscribers)` past its pre-request value, then fire.
- **F-2 LOW** `tests/test_daemon_r2026_09_12_bundle.py:230` (1 s hold at `:204`): `purged_at >= done_at - 0.05` compares monotonic stamps across two threads and two round trips; the row counts already prove the invariant.
  - DRIVEN (counts alone kill P03 3 of 3). Class 21. Fix: delete the clock assertion; gate the purge on an event set while the lock is held.
- **F-3 LOW** `tests/test_daemon_startup.py:190`: `"9870" not in quiet` matches a `free_port()` such as 39870.
  - REASONED. Class 21. Fix: assert `"127.0.0.1:9870" not in quiet`.
- **F-4 LOW** `tests/test_cli_r2026_09_12.py:488`, `:499`: version-gate tests pass `-o x.csv` without `monkeypatch.chdir(tmp_path)`; a regressed gate writes `x.csv` into `host/`.
  - DRIVEN (P43 left `x.csv`). Class 33. Fix: `monkeypatch.chdir(tmp_path)`.
- **F-5 LOW** (pre-existing) `tests/test_session_bundle.py:205-207`, `tests/test_review_r2_server.py:119-121`: `len(during) == 1` right after headers races the background temp-file delete.
  - DRIVEN (each flaked once under unrelated mutants). Class 21. Fix: hold the body open with a slow iterator, or drop the `during` half.
- **F-6 LOW** `tests/test_decode_per_port.py:105` pins the name-merged fallback for a detached board (same item as C-3; owner decision).
- **F-7 LOW** `tests/test_daemon_r2026_09_12_server.py:173-186`: "session with no lines answers empty" asserts only 200.
  - REASONED. Class 29. Fix: assert empty `lines`/`frames`/`points` and a header-only CSV.
- **F-8 LOW** `tests/test_webui.py:165`: `DIALOG_IDS` is hand-kept; a new `$("x")` is never checked against `index.html`.
  - DRIVEN (139 ids scanned, all present today). New class candidate: a hand-kept list standing in for a mechanical enumeration. Fix: derive the list by regex from the modules.
- **F-9 LOW** `store.py:618` with `:1051`: a subscriber arriving after `stop_subscribers` gets no sentinel (same as C-2).
- **F-10 MED** `store.py:988` (P31 survived): folding a bare `\r` is untested. Class 29. Fix: test `raw = "a\rb"`.
- **F-11 MED** `store.py:1581` (P33 survived): reverting `_window_id_ceiling` to `ORDER BY ts DESC LIMIT 1` passes. Class 29. Fix: in the clock-step fixture assert `_window_id_ceiling(T0 + 2) == 8` (old form answers 3).
- **F-12 MED** `cli.py:1017` (P75 survived): a live follow learning a filtered-out `!pd` under its own port is untested. Class 29. Fix: follow test with a hidden `!pd` redefinition before a shown sample.
- **F-13 MED** `webui/can.js:600` (J15 survived): the export watermark of a paused CAN table is untested. Class 29. Fix: open CAN export while paused, assert `id_to` equals the frozen id.
- **F-14 MED** `webui/can.js:591` (J16 survived): `canShownLastMs` untested. Class 29. Fix: assert `last_ms` with two rows whose `lastTs` differ.
- **F-15 LOW** `webui/can.js:97` (J12): frozen age clock untested across a paused rebuild. Class 23.
- **F-16 LOW** `webui/can.js:208` (J22): the `0.9995` s boundary untested. Class 29.
- **F-17 LOW** `webui/exportdlg.js:186` (J29): the portless `-` case untested (reachability doubtful: test it or delete the branch).
- **F-18 LOW** `webui/state.js:312` (J38): `/can/frames` as streamable untested. Class 29.
- **F-19 LOW** `webui/timewindow.js:226` (J40): 1 s anchor-thinning gap untested. Class 29.
- **F-20 LOW** `webui/timewindow.js:229` (J41): `ANCHOR_CAP` untested. Class 29.
- **F-21 LOW** `webui/terminal.js:44` (J44): `-` for a gap row in the tick column untested. Class 29.
- **F-22 LOW** `webui/terminal.js:219` (J50): `row.id > frozenId` in `renderEmpty` untested. Class 23.
- **F-23 LOW** `webui/plots.js:860` (J55): paused guard on chart zoom untested. Class 25.
- **F-24 LOW** `webui/digital.js:296` (J62): the same guard for lanes untested. Class 25.
- **F-25 LOW** `webui/plots.js:1109` (J60): tick-mode cursor for a line with no tick untested. Class 29.
- **F-26 LOW** `webui/digital.js:241` (J65): lane colour applied to every port's lane of that name untested. Class 29.
- **F-27 LOW** `server.py:2000` (P13): WS tail's clean return on the stop sentinel untested. Class 29.
- **F-28 LOW** `server.py:3086` (P29): which port's group name labels a wide-CSV column untested. Class 29.
- **F-29 LOW** `server.py:3091` (P73): a stream first declared inside the export window (loop body never runs in any test). Class 29.
- **F-30 LOW** `store.py:2018` (P38): dirty rebuild in `plot_ports_safe` unreachable from its only caller. Fix: mark dead by design, or drive a purge between the awaits.
- **F-31 LOW** `daemon.py:289` (P66): exit 3 on a bind failure has no test; every test stubs `_serve` (same as C-9). Class 27.
- **F-32 LOW** `protocol.py:985` (P69): `declared_kinds` excluding a bits group's own name untested. Class 29.

## Sweeps

- **Class 21, clock and sleep** (10 sites): `bundle.py:204, 215, 222` violate (F-2); `server.py:211, 241` violate (F-1); `decode_per_port.py:35, 44`, `test_sim.py:885` exempt (ts values, no ordering); `terminal_filter_pane.test.mjs:60, 63` comply.
- **Class 21, absence assertions** (28 sites): `test_daemon_startup.py:190` violates (F-3); the other 27 comply (fixture text, no clock values).
- **Class 27** (48 sites): `test_capture_lock.py:185`, `test_daemon_startup.py:176, 203` violate (`_serve` stubbed, F-31); the rest comply (MockTransport runs the real client paths, JS offline throwers are a real offline state, canned fetch bodies match the daemon, and the export guard double has a contract test).
- **Class 28** (9 sites): 5 `raise AssertionError`, 1 `except BaseException`, 3 `pytest.raises`: all comply.
- **Class 29**: 30 refusal guards mutated, 30 killed; 15 negative-state guards, 6 killed, 9 survived (F-10, F-13 to F-15, F-22 to F-24, F-27, F-30).
- **Class 33** (7 sites plus cwd): comply, except `test_cli_r2026_09_12.py:488, 499` (F-4).
- **Class 50** (2 doubles): `bundle.py:77-81`, `:200-210` comply.
- **Class 63** (63 hits): comply; `test_timeline.py:259` omits `truncated` (a shape the daemon never sends, read as false, harmless).

## The two questions

1. Least confident: kill attribution (two kills were first credited to flakes, so every Python kill was re-run alone; JS kills were not); four equivalence rulings (P27, P56, J31, J32) are reasoned; F-9 reasoned only; Windows not run.
2. Not checked: coverage cannot see subprocess CLI tests; the 79 JS mutants were picked by reading, not enumerated, and `statusbar.js`, `settings.js`, `chrome.js`, `cmdbar.js` got only 11 between them, so more survivors likely exist there.
