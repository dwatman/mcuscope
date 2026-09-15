# Browser leg: dialogs

Headless Chromium (playwright 1.62.0) against `mcuscoped --config` on 127.0.0.1:8591, boards from `mcu-sim --demo` (9911) and `mcu-sim --flood 10000` (9912).
The large capture is one 603358-line auto session (`build_large.py`, reused as a copy per run).
Scripts, outputs (`*.out`), event traces (`*.json`) and downloads (`dl/`) are in `~/tt-data/browser-leg/dialogs/`.
The only console errors seen are the browser's own "Failed to load resource" lines for the expected 400/401/409 answers and the refused `/ws` handshake in item 10.

## Results

- PASS: Config warnings.
  - `bogus_key = 1` under `[storage]`: one `li` reading `config: unknown key 'bogus_key' in [storage], ignored`, visible, placed below `#cfgPath` (the TOML path).
  - Restarted on the clean file: `#cfgWarnings` hidden with 0 items, after the same `/status` read filled `0 = no cap; now 68 kB`.
  - Script: `s12_config.py`.
- PASS: Config revision.
  - Tab B saved Updates (file changed on disk). Tab A then typed retention 42 and min sessions 7 and saved Storage.
  - `#cfgStorageErr` reads exactly `config file changed since it was read; reload it and try again` (SPEC 3.3.1 text, no "reopen"). Fields still read 42 and 7, section `dirty` with `Save *`, file unchanged by A.
  - Script: `s12_config.py`.
- PASS: Settings > Sessions.
  - Run `doomed` (id 4) deleted from tab B's Settings. Export on tab A's stale row: the action strip reads `session export failed: no such session: 4`, and there were 0 downloads in 3 s.
  - Positive control on the same listener: export of `keep` downloaded `/sessions/2/export` as `keep.db`.
  - Script: `s3478_export.py`.
- PASS: Export preflight, deadband.
  - Chart export with changes on and deadband `ftest:0.5`: `#expErr` reads `plot export failed: deadband needs name=value: ftest:0.5`, the dialog stays open, 0 downloads in 3 s.
  - Control: with the deadband blanked, Export downloaded a `/plot/export` CSV and closed the dialog.
  - Script: `s3478_export.py`.
- PASS: Export preflight, token-less `/lines/export` of the large capture.
  - Playwright events: `request`, `response 200` (5 ms), `requestfailed net::ERR_ABORTED` (1 ms later; CDP `loadingFailed canceled=true`), then `download` for the same `/lines/export?session=1&format=text` URL.
  - The download event came 116 ms after the click and the 38.5 MB body completed at 25.3 s, so the body streamed after the event.
  - The download navigation itself is browser-level: it shows in neither page request events nor page CDP, only as the download event (URL is the export path, not `blob:`). Trace: `s5_requests.json`.
  - Script: `s56_large.py`.
- PASS (proxy as briefed): FW-9.
  - Through a forwarder capped at 10 MB/s, the 82.9 MB session `.db` with no token: download event at 5.1 s, body complete at 13.5 s.
  - Contrast with a stored token (blob path): headers at 5.7 s, download event only at 14.0 s, complete 14.3 s.
  - The literal "at once" does not hold for a large run: the download starts 4.7 to 5.2 s after the click (3 runs), which is the daemon building the temp copy before sending headers (`server.py export_session`). See Defects for what the button does in that window.
  - Script: `s56_large.py`.
- PASS: FW-1 after-CONT half.
  - With the daemon SIGSTOPped: pane export, Export, Cancel, then chart export. Title `Export plot data`, Export enabled on open.
  - 2.00 s later: options `["whole capture"]` and `could not list sessions: no reply from daemon`.
  - After SIGCONT: 0 downloads in 5 s. Control: that dialog's Export then downloaded a `/plot/export` CSV.
  - Script: `s3478_export.py`.
- PASS: Export preflight after-CONT half.
  - Pane dialog with the session list filled, daemon SIGSTOPped, Export: `lines export failed: no reply from daemon` after 2.08 s, dialog open.
  - After SIGCONT: 0 downloads in 5 s. Control: Export again downloaded the `/lines/export` text file.
  - Script: `s3478_export.py`.
- PASS: Attach `devSel`.
  - `showModal` focuses `#devSel` (the `autofocus`), which holds only `loading devices...` while `/devices` is held by `page.route`. Attach is disabled.
  - Keys `c`, `s`, `l`, ArrowDown and End during the hold: index stays 0, no `input` or `change`.
  - After release: options `socket://127.0.0.1:9900 - simulator (tcp)` and `custom...`, index 0 selected, alias prefilled `board`, focus still on `devSel`, and 0 events from the replacement.
  - Positive control: `c` after the list lands fires `input:custom` and `change:custom` on the same listener. The type-ahead buffer was left to expire first; without that pause the `c` was swallowed.
  - Trace: `s9_devsel.json`. Script: `s9_11_attach.py`.
- PASS: FD2-7.
  - The guard exempts only `127.0.0.1`, `::1` and `::ffff:127.0.0.1` (`server._LOOPBACK_CLIENTS`).
  - Driven from loopback by an in-script forwarder (127.0.0.1:9913) whose upstream socket binds 127.0.0.2. Setup check: `/status` direct 200, via forwarder without token 401, with token 200.
  - With stored token `wrong-token-x1`, the forwarder saw `GET /ws?token=wrong-token-x1` get `HTTP/1.1 403 Forbidden`, and the page saw close code 1006.
  - One prompt, answered with the right token at 332 ms. The next `/ws` was built at 1345 ms, exactly one 1000 ms backoff after the close. It got `101` and opened, and the stream warning was hidden.
  - Note: the 401 that raised the prompt was `/config` (the restart-badge prime in `initSettings`). `/status` got its 401 next and reused the entered token without a second prompt.
  - Script: `s10_token.py`.
- PROXY: E-11.
  - Custom baud 0: `#dlgErr` (`role=alert`) reads `Baud must be 1-100000000`. `ping` result lands in `#cmdResult` (`role=status`) (spans `>`, `ping`, `ok`, `monitor 1 sim`, `1.8 ms`).
  - `#cmdResult` is `hidden` until it gets text, so whether a screen reader announces it stays a manual check.
  - Resizer `role=separator`, min/now/max `260/360/954` on load (sidebar 360 px). ArrowLeft gives now 380. 60 Shift+Left clamps at 954 = max, 60 Shift+Right clamps at 260 = min.
  - Script: `s9_11_attach.py`.

## Defects

- Found while scripting FW-9, not a listed item: a session `.db` export downloads twice on a second click during the daemon's build.
  - Repro: no token, a 603k-line run. Settings > Sessions > export, click export again 300 ms later.
  - Observed: the button is enabled again at 300 ms, and two downloads of `/sessions/1/export` arrive at 5.0 s and 5.4 s, so the daemon built the copy twice.
  - Expected, per the comment in `settings.js sessionRow`: "The button is held until the download is away, so a double click downloads once." On the navigation path `downloadPath` returns right after the anchor click, so the button is released while the daemon is still building. There is no UI feedback during that build either.
  - Script: `s6b_double.py`.
