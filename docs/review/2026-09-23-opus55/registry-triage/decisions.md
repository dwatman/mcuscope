# Decisions for the owner

Each question stands alone. Option A is the recommendation and the batch default; the batch implements whatever you pick.

## Registry leg picks

### D-1 (R1-1 HIGH) How to stop a short regex from stalling or exhausting the daemon
A 24-character nested-repeat pattern compiles for about a second on the event loop; a 45-character one runs a capped daemon out of memory. Any client that may read can send it (`/lines?match=`).
- **A. Refuse patterns whose nested repeat counts multiply past 100,000, and compile off the loop.** Closes both the stall and the memory; `\d{65535}` stays legal.
- B. Compile off the loop only. Fixes the stall, not the memory.
- C. Compile in a child process with a memory cap. Complete, but a process per request.

### D-2 (R15-2) The sdist ships tests that cannot be collected
Five test files import `mcu_sim` from the repo's `tools/`, which the sdist does not have.
- **A. Import `mcuscope.sim` in those five; one shim test skips when `tools/` is absent.** Five-line change, the sdist stays self-testing.
- B. Drop `tests/` from the sdist.

### D-3 (R17-1) What a relative `storage.db_path` means
Today it is relative to the daemon's CWD, so `mcu daemon restart` from another directory opens a new, empty capture, and every surface still shows `rel.db`.
- **A. Relative to the config file's directory, reported absolute everywhere.** Stable whatever directory starts the daemon.
- B. Relative to the startup CWD, reported absolute. Honest, but restart still moves it.
- C. Refuse a relative path at load.

### D-4 (R17-4) Which pid `mcu daemon start` prints on Windows
Under a venv launcher the spawned pid is the launcher; `/status` `pid` is the serving process. `taskkill` on the printed pid hits the launcher.
- **A. `started mcuscoped (pid <serving>; launcher <spawned>)` when they differ; `--json` `pid` serving plus `launcher_pid`.** Both are useful, the first is the one to act on.
- B. Keep the spawned pid (it matches the pid record) and document it.

### D-5 (R19-2) Pane regex vs daemon regex on non-ASCII text
The pane (JavaScript) reads `\d \w \s \b` as ASCII, the daemon (`regex`) as Unicode, so a pane shows a marker `reading ٣` live and hides it in history and export. Only markers carry non-ASCII text.
- **A. Daemon compiles user patterns with ASCII classes; the pane rewrites `\s` to the ASCII set.** One meaning everywhere; `.` over emoji stays a documented residual. Changes `mcu lines --match '\w'` on non-ASCII markers.
- B. Keep both, record the difference in the fixture and SPEC 9.
- C. Refuse `\d \w \s \b` in pane filters.

### D-6 (R19-3) Whitespace in a host marker's text
The web UI trims and refuses blank; `mcu mark` and `POST /marker` store `"   "` and `"  x  "` verbatim.
- **A. The daemon strips (as it does session names) and refuses blank.** One rule at the one place every client passes.
- B. Store verbatim everywhere; the UI stops trimming.

### D-7 (R20-2) Cost of `mcu can dump -f` on a quiet port
A follow that never sees a frame re-reads every frame above id 0 every 0.2 s: 30% of a core on a 1M-line capture, growing with the capture.
- **A. `/can/frames` answers `next_since_id`, the CLI advances on it.** Each poll costs only what is new. Adds one response field (SPEC 3.4).
- B. CLI starts its watermark at the newest line id; the store drives port-filtered reads from the port index. No API change; cost then grows with the follow's lifetime.

### D-8 (R22-4) Grammar of query and path parameters
They parse leniently: `DELETE /sessions/+2` and `/sessions/%203%20?data=yes` delete. Bodies are already strict.
- **A. Ints ASCII digits, floats ASCII decimal, bools only `true`, `false`, `1`, `0`.** A script sending `data=yes` then gets 422.
- B. Tighten ints and floats only; keep pydantic's bool spellings.
- C. Tighten the destructive routes only.

### D-9 (R22-6) What "non-space" means in a marker and a command
SPEC 2.5 says "non-space" and "surrounding whitespace" without defining them; the host uses Python's whitespace set, the firmware space and tab, SPEC 2.1 makes only U+0020 a separator.
- **A. U+0020 only, host and firmware.** Matches SPEC 2.1's tokenizer; `ping\x1f` then reaches the board as typed.
- B. Space and tab (the firmware's set) on both sides.

### D-10 (R25-1) A pane added after "clear all"
It starts with every line clear-all just hid, with negative relative times.
- **A. Clear-all records a watermark; new panes start there.** Per-pane clears stay per pane.
- B. The new pane copies the clear point of the pane it is cloned from.

### D-11 (R25-2) SPEC 9.2 against the pinned F-23 test
SPEC 9.2: while a zoom stands "every window selector shows a chip". F-23 pins a chart born live under the zoom as not on it, yet its selector shows the chip.
- **A. SPEC reads "every window selector of a surface frozen on it"; a live chart lights its own span.** The label then matches the state.
- B. Keep the SPEC wording; a new chart is born on the zoom (reverses F-23).

### D-12 (R26-1) Tick estimates on a long-paused pane
After about 3 h of pause at a steady board the per-port anchor store rotates and `~N` estimates turn into `~-`.
- **A. Snapshot the needed anchors at pause.** The frozen view reads a frozen input, as its rows do.
- B. Memoize each row's estimate when first drawn (a later history page cannot improve it).

### D-13 (R27-3) Firmware `can filter all` after a hardware mask filter
`all`/`none` change only the software filter; a shim that programmed a hardware filter keeps discarding, while `can filter all` answers OK.
- **A. `all` also calls `mon_can_filter(bus, 0, 0, false)`; the contract says a zero mask opens both id kinds.** The three vendoring projects then need their shims checked against that line.
- B. The contract forbids narrowing hardware filters (software filter only).

### D-14 (R36-1) `mcu-sim --flood` after a stall
A 1 h stall is paid back at the burst cap: 75 M lines at 500,000/s against 20,000/s requested.
- **A. Re-anchor after a stall (drop the backlog).** The flood is a rate; its lines are synthetic.
- B. Exempt: the backlog is load the operator asked for.

### D-15 (R51-1) Pane history walk that finds nothing in 5 pages
It stops with the hint "scroll to the top for older lines" while the view is already at the top, where no scroll can happen.
- **A. The hint becomes a "search older" control.** One click per 5 pages, no unbounded walk.
- B. Keep walking until rows land or the capture ends (unbounded requests on a filter matching nothing).
- C. Exempt the residual.

### D-16 (R53-3) `mcu assert` on an empty scope against a 0.4.0 daemon
0.4.0 answers `pass` with 0 lines; SPEC 4 promises `empty`, exit 1. `DAEMON_MIN_VERSION` (0.4.0) admits it.
- **A. The CLI judges `pass` with 0 lines and no `--allow-empty` as empty (text, exit 1, `--json` status).** No extra request; correct against any daemon.
- B. Gate `mcu assert` on daemon 0.5.0 (one `/status` per assert, refuses 0.4.0).

### D-17 (R71-1) Storage bounds that disagree between the loader and the API
The loader accepts `retention_days = 5000`; the dialog and `PUT /config/storage` refuse it, so every Storage save fails, even of another field.
- **A. One set of bounds in `config.py`, used by loader, API and dialog (the loader's wide ones).** No value the file holds can block a save.
- B. Keep the API's narrow bounds and let an unchanged loaded value through.
- C. Narrow the loader to the API bounds and warn.

### D-18 (R72-1) Token prompt opened by a background request
A 401 on the 5 s poll opens `window.prompt`, which takes the keystrokes of a marker being typed and can store them as the token.
- **A. A background 401 marks "token needed" on the chip; the prompt opens on the next user action.**
- B. Exempt: the prompt may be the only way to ask.

### D-19 (R77-1) A backward host-clock step on charts and lanes
Every sample until the clock passes the old high-water draws at the same x, and the lanes' live edge stops for the length of the step (an hour, for an hour's step).
- **A. A backward step over 1 s breaks the series like a tick restart; smaller steps keep the nudge.**
- B. Out of scope for class 77; document.

## Overnight decisions of 2026-09-24 to confirm (REVIEW_LOG "Owed")

1. Firmware F3, a cut event with no token past its header sends only the overflow notice: keep, nothing else is decodable.
2. Firmware footprint, own formatter kept (about +0.45 KB where `snprintf` is linked anyway): keep, most boards do not link it.
3. Chrome F2, `.db` navigations add `wait=1` and queue; fetch exports keep the 503: keep.
4. Store 3, a stamp inversion past 10 s is announced by sys rows, not a `/status` counter: keep, it lands in the capture where the gap is.
5. CLI 3, text export carries `[port]` when more than one port is attached or stored: keep; R25-3 extends the same rule to `tail -f`.
6. Link F1, Windows console-close hold keeping an inherited ignore-Ctrl-C: keep, pending the Windows check in `windows.md`.
7. Config, a duplicate port alias keeps the last entry: keep, the loader warns naming it.
8. `daemon start` index build: "Ctrl-C leaves it building (pid N)", 600 s ceiling then exit 1 with the daemon running: keep.
9. `!p` cut keeps its tick; a partly filled gap divider moves above the loaded page with the remaining count: keep.
10. Verdicts judge only rx rows unless `chan` names a host channel; `port=""` selects the daemon's rows; the CLI refuses empty `-p`: keep.

## Owner rulings (2026-09-25)

- Option A for D-1 to D-12, D-14, D-15 and D-17 to D-19, and for the R63-2 wording ("not judged" for a forbid when the send failed).
- D-13: neither option. The monitor observes and must never change hardware configuration the firmware relies on. `can filter` stays software-only; no monitor path or shim hook may program a CAN hardware filter, and the contract (`INTEGRATION.md`) says so.
- D-16: no backward compatibility before v1.0. Raise `DAEMON_MIN_VERSION` to the current version so the CLI refuses older daemons; this also closes R53-1 and removes the need for per-command version checks.
- The ten overnight decisions of 2026-09-24: all kept.

### Earlier open picks and agent judgement calls (REVIEW_LOG "Owed"), ruled 2026-09-25

- Bundle export: take the export slot before the sweep lock, so retention never waits on the queue (daemon-api).
- Web UI `.db` download past the queue cap: the UI pre-checks for a slot with a fetch before navigating and shows a refusal itself (webui-panes, server side daemon-api).
- Reload badge: compare `/status` against the version of the daemon that served the page, not the first `/status` seen (server side daemon-api, `statusbar.js` webui-settings).
- Over-long `!p` at high rate: one overflow notice per episode, with a count when it ends (firmware-packaging).
- A copy interrupted exactly at open reports "interrupted", not "unable to open database" (daemon-api).
- `last_ms`: kept; SPEC 3.4 says "newest line by id" explicitly (daemon-api). `WINDOW_TS_SLACK_S` stays 10 s.
- Kept as is: `/marker` unknown port 400, `mcu send -` refused, a failed `--send` ends the window, the 1 s live-scan grace.
