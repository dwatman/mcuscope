# Browser checks that need the owner

Each needs a browser, OS or tool the scripted leg does not have (headless Chromium on Linux only).
Setup for all: `mcuscoped --sim --config <throwaway TOML with its own db_path>` on port 8558, and open `http://127.0.0.1:8558/ui/`.

Status 2026-10-06: O-1 to O-6 passed in headless system Firefox 156 (puppeteer-core, `~/tt-data/mcuscope-tools/browser-firefox/`); O-9 passed against Chromium's accessibility tree (not real speech) and O-10 with the step simulated (`browser/owner/o9.py`, `o10.py`), its reload sub-check correcting SPEC 3.4 and 9.2. The CAN/Plots divider is now keyboard-reachable. Owed: O-7, O-8 (Windows). O-11 and O-12 ruled 2026-10-06: light-theme amber and port tags meet 4.5:1 on every surface (amber `#8f5a14`, port tags mixed 50% toward the text colour), and the sidebar minimum is 300 px.

## Firefox

- O-1 Session chip: POST a session named `'N'*20 + '‮gpj.exe' + 'x'*32` (`python3 -c` with urllib), look at the header.
  - Expected: one row, the chip ends in an ellipsis and reads `■ NNNN…<U+202E>gpj.exe…`; the header does not grow.
- O-2 Reload badge after Back: open the UI, in the same tab go to `/status`, stop the daemon, set `__version__` in `host/mcuscope/__init__.py` to `0.5.1`, restart it, press Back (revert the edit after).
  - Expected: the tab shows `daemon updated: reload`, or it reloaded fresh with version 0.5.1 in the brand and no badge.
- O-3 Sec-Fetch: `python3 -m http.server 8812 -d ~/tt-data/mcuscope-tools/browser/owner`, open `http://localhost:8812/` and then `http://127.0.0.1:8812/` with devtools Network open.
  - Expected: `status?via=img` and `status?via=fetch` are 403 on both; the `UI` link opens the UI; the `/status` link is 403.
- O-4 Decimation: set the chart window to 30 s and let it run 40 s.
  - Expected: the sine and the lanes look like `g1-owed/decim/decim-*.png` in the output dir: no block steps on the sine, no solid blocks on the lanes.
- O-5 Five session exports: on a capture with a session of about 500k lines, start four `curl -so /dev/null http://127.0.0.1:8558/sessions/<id>/export &`, then Settings > Sessions > export on the same run.
  - Expected: after a wait (Chromium: 14 s) a `.db` saves whose first bytes are `SQLite format 3`, not JSON; a wait near 300 s may hit Firefox's response timeout.
- O-6 Regex box: click a pane's regex box (it widens), type `sim`, click its `x`.
  - Expected: the box empties and keeps the caret.

## Windows

- O-7 Bidi isolates: start a session named `run`, open Settings > Sessions > delete (Cancel the confirm), then any export dialog's session list, in Chrome and in Firefox.
  - Expected: the name shows with nothing around it, no boxes (U+2068/U+2069 render as nothing).
- O-8 Optional, 125% display scaling: host and tick base, 5 s and 30 s windows.
  - Expected: no x-axis label cut at either end of a chart (Chromium at DPR 1.25 and 1.5 passed, `g1-owed/labels`).

## Screen reader (E-11 and the 2026-09-14 / 2026-09-12 announcement lines)

- O-9 With Orca (or NVDA): Attach with baud `custom` = `abc`, Attach; type `ping` in the command bar, Enter; Tab to the divider; open each dialog; Tab through Attach and Export.
  - Expected: the baud error is announced; the command result is announced; the divider reports a px range; each dialog's title is read; Attach fields read label and hint (serial number and line ending included); Export's Format, Deadband and checkboxes read their labels.

## Needs sudo

- O-10 Host wall clock stepped back: charts, lanes and the CAN table live, then `sudo timedatectl set-time` 30 s back (restore NTP after).
  - Expected: a glued edge and stale CAN ages, as SPEC 9.2 documents.

## Owner calls (measured, not judged)

- O-11 Light theme warning colours: amber `#b5751a` is 3.8:1 on white (CAN stale age, `N below` cue, `paused` tag) and a chart head's port tag in its port colour is 2.4:1 (bench lilac).
  - Both sit below the 4.5:1 AA line the light accent was raised to; decide whether status colours must meet it (`g2-chrome.md`).
- O-12 CAN table at the 260 px minimum sidebar width scrolls sideways and hides the age column (`g4-can/wrap/wrap-260.png`); from 300 px up it fits.
