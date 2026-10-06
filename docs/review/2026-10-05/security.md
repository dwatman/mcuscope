# Security leg, 2026-10-05

Base 5eaaeeb. Probes and logs in `~/tt-data/mcuscope-2026-10-05/security/` (scripts at the top level, one `work/<probe>/` per run with `run.log`).
Every daemon ran on port 18600 with its own TOML, `db_path` and `MCUSCOPE_*_DIR` in scratch; fake boards on 18601-18607.

## SEC-1 HIGH CONFIRMED: any local process, any local user, has full control of the daemon and acts as the daemon's user

- Where: `host/mcuscope/server.py:781` (`_LOOPBACK_CLIENTS`), `server.py:871` (token guard exempts loopback), `server.py:1297` (`_config_write_denied` passes loopback), SPEC 3.1 (`docs/SPEC.md:373`).
- Contradiction: SPEC 3.1 says "the local machine is the trust boundary"; this round's threat model names other local users on a shared machine as attackers.
  One of the two is wrong for a shared machine; I think the SPEC should change, since the daemon turns a TCP connection into the daemon user's file and device access.
- Failure: a loopback TCP connection carries no uid, so another account on the box gets everything the owner has:
  - Read the capture (`GET /lines`, exports), drive the board (`/cmd`, `/send`, `/break`), `POST /shutdown`, `PUT /config/*` (including `db_path`), purge sessions.
  - Confused deputy: `POST /ports` with a bare device path opens any device the daemon user can open.
    Attaching the owner's terminal (`/dev/pts/N`) put it in raw mode (lflag 35387 -> 0), captured what was typed there into `/lines`, and `/send` wrote an OSC 52 clipboard sequence to that terminal's display.
  - Port squatting: whoever binds 127.0.0.1:8558 first is "the daemon" for the owner's `mcu` and browser (fake verdicts to an agent, a phishing UI on the trusted origin). `mcu daemon start` checks its own start id; other commands cannot.
- Repro: `pts.py` (opens a pty pair as a stand-in for the victim's terminal, attaches the slave through the API, types `MyS3cretPassw0rd` on the master):
  `captured ['\x1b]52;c;...injected', 'MyS3cretPassw0rd', 'sudo -S true', '>1 ping', ...]`, `victim display got b'>1 ping\n\x1b]52;c;ZWNobyBwd25lZA==\x07injected\n'`.
  Not driven as a second uid (no second account here); the cross-user step is ordinary TCP loopback semantics.
- Fix options, owner's call:
  - A per-user secret: the daemon writes a random token to a 0600 file in the data dir at start, `mcu` reads it and sends it on loopback too, `--open` hands it to the browser (URL fragment, then localStorage).
  - Or, Linux only and cheaper: refuse loopback connections whose peer socket is owned by another uid (look up the peer's inode in `/proc/net/tcp{,6}`). Windows has `GetExtendedTcpTable` with owning pid for the same check.
  - Whatever the choice, SPEC 3.1 should state the shared-machine position explicitly.

## SEC-2 HIGH CONFIRMED: never-repeating ad-hoc channel names grow daemon memory without bound; `/plot/channels` then returns hundreds of MB

- Where: `host/mcuscope/store.py:723` (`_plot_summary`, one entry per (port, name), held until the rows age out), `server.py:2071` (`/plot/channels` lists every entry in one response).
- Failure: a firmware emitting `!p <tick> n<k>_0=1 ... n<k>_14=1` with a fresh `k` each line (hostile, or a bug printing an index into the name):
  - RSS 176 MB -> 762 MB in 43 s and still climbing linearly (about 330 B per name), against a control run of the same line rate with 105 recurring names that plateaued at 145 MB (`rotate_noq.out` vs `rotate_fixed.out`).
  - `/plot/channels` after 5 s: 828,615 channels, 174 MB body, 33 s; after 45 s: 1.74 M channels, 368 MB, 35 s, RSS 1.1 GB (`rotate.out`).
  - During one such response `/status` stalled up to 2.28 s (idle max 0.002 s) (`rotate_loop.out`): the loop is partly blocked while it is built.
  - At a real 115200 baud that is roughly 950 new names/s, about 1.1 GB/h of RSS: an unattended weekend capture ends in an OOM kill, retention (10 days by default) does not help.
- The web UI is fine on top: it loaded and capped at its own `MAX_CHANNELS` against 576k names (`rotate_ui.out`). CAN ids rotating through 2^29 do not grow daemon memory (`rotate_can.out`).
- Fix: cap distinct ad-hoc names per port in the decoder (store the overflow as generic events and announce it once with a sys row and a counter, per the no-silent-drop rule), and page or cap `/plot/channels`.

## SEC-3 MEDIUM CONFIRMED: CSV formula guard bypassed where the list separator is `;`

- Where: `host/mcuscope/server.py:3562` (`_csv_cell`): guards only the first character of the cell and quotes only on `,` `"` CR LF.
- Failure: Excel in de/fr/nl/... locales (and LibreOffice with `;` ticked) opens a `.csv` with `;` as the separator.
  A device line `temp ok;=1+41` exports as an unquoted cell; the reader splits it at the `;` and the second cell `=1+41` evaluates.
  The docstring's own promise ("must neither execute on open in a spreadsheet") does not hold.
- Repro: `csvinj.py`: `/lines/export?format=csv` with a fake board printing `temp ok;=1+41`, converted by headless LibreOffice with `;` separator and formula evaluation: the cell reads `42`.
  The leading-char guard does work (`=2+40` exported as `'=2+40`, stays text), and `@`/`-` after `;` did not evaluate in LibreOffice.
- Quoting the cell is not enough: `1,"temp ok;=1+41"` read with `;` still yields `=1+41"` as a cell start (LibreOffice evaluated it to `Err:509`), because the quote is not at a field start.
- Fix: neutralise a formula character after every `;` and TAB inside a text cell too (same `'` prefix), or write an Excel `sep=,` first line (Excel-only; LibreOffice shows it as a row). Only the `raw` column (and marker text) carries free text; names, labels and hex are grammar-restricted.
  `can.js:605` `csvField` mirrors the rule but only ever sees aliases and hex.

## SEC-4 MEDIUM CONFIRMED: request bodies are buffered whole, and a 422 echoes the entire rejected value back

- Where: no body-size limit anywhere in `server.py`; `server.py:623-624` puts `repr(input)` of the failing field into the error.
- Failure:
  - `POST /marker` with a 300 MB `text` (the field is capped at 4096): daemon RSS 59 MB -> 1.6 GB peak before the 422 (`body.out`). A 2-3 GB body OOMs the daemon on a 16 GB machine.
  - The 422 for a 10 MB value is itself 10,485,829 bytes: the whole input, repr'd (`body10.out`).
- Who can send it: any local process (SEC-1), and any LAN client of a tokenless `0.0.0.0` bind (the token guard runs before the body is read, so a tokened bind is safe).
- Fix: an ASGI middleware refusing `Content-Length` above a small cap (64 KB covers every body the API takes) with 413, and counting streamed bytes for chunked bodies; cut `got` to e.g. 80 characters.

## SEC-5 LOW CONFIRMED: device SGR sequences pass to the terminal and outlive their line

- Where: `host/mcuscope/cli_output.py:49` keeps every `ESC [ ... m`.
- Failure: a line ending `\x1b[8m` (conceal) with no reset hides every later line `mcu tail`/`lines`/`assert` prints, including the CLI's own verdict, and leaves the shell prompt invisible after exit. `\x1b[30;40m` does the same with colour.
- Repro: `sgr.py`: marker `calibration done\x1b[8m`, then `mcu tail -n 5` on a pty: bytes after the conceal contain no reset (`...done\x1b[8m\r\n... SECOND LINE\r\n`).
- Fix: in `visible()`, append `\x1b[0m` after any line that carried an SGR, or keep only colour SGRs (30-37, 39, 90-97, 0) and escape the rest.

## SEC-6 LOW CONFIRMED: the capture and data files are created with the default modes

- Where: no `chmod`/`umask` in the package; `lockfile.py:124` opens with `0o644`; SQLite creates `capture.db` 0644; dirs follow the umask (0775 here).
- Failure: where the home directory is traversable (0755 is still common outside recent Ubuntu), other local users read the whole capture, including when no daemon runs. Here home is 0750, so not exploitable on this machine.
- Repro: `find work/xsite -printf '%m %p'`: `644 capture.db`, `644 capture.db.lock`, `664 data/...startup.log`, `775 data`.
- Fix: create the data dir 0700 and the DB (plus `-wal`/`-shm`) 0600 on POSIX.

## SEC-7 LOW SUSPECTED: the wrong-token lockout is per exact address, so an IPv6 LAN client is not throttled

- Where: `server.py:812-846`.
- Reasoning: records are keyed by the full client address and the table evicts its oldest entries past 1024. A client on a `::` bind can rotate through its /64 and get 10 guesses per address, so SPEC 3.1's "throttled to a rate at which any realistic token is unguessable" holds only for long random tokens; a short token only warns at start.
- Confirm by: binding `::` and cycling source addresses from a second host. Fix: key IPv6 records by /64 and add a global failure budget, or refuse tokens under 16 characters on a network bind.

## Checked and fine

- Web UI XSS (`xss.py`, headless Chromium): HTML/script payloads in debug, RESP, `!e`, `!m`, `!pd` unit, CAN data, session name and note, API marker; `window.__xss` never set, no injected `img`/`svg`/`on*` attribute/non-http href, no dialog, no page error; payload text visibly rendered (positive control).
- Prototype keys from the wire (`__proto__`, `constructor`, `toString`, `valueOf`, `hasOwnProperty`, `isPrototypeOf` as ad-hoc names, stream names, enum labels, bit lanes): charts render them, `Object.prototype` untouched, no errors (class 34 holds).
- No `innerHTML`/`insertAdjacentHTML`/`eval` in `webui/*.js`; vendored uPlot 1.6.31 has no `innerHTML`; update link href validated to http(s).
- CLI on a pty (`cli_esc.py`): `tail`, `tail -f`, `lines`, `log export` (text and `--csv`), `cmd`, `status`, `ports`, `session list`, `wait`, `assert`, `plot list`, `can list`, a bad command argument: OSC 8, OSC 52, OSC 0 title, BEL, CSI 2J, C1 all shown escaped; only SGR passes (SEC-5).
- Cross-site from real Chromium (`xsite.py`, attacker on `evil.example` mapped to 127.0.0.1): form POST text/plain, no-cors fetch POST (text/plain and JSON), `sendBeacon`, `<img>` to session export/bundle/lines export, WebSocket, cross-origin read, top-level navigation to an API route: all refused, nothing landed in the capture, no session created.
- DNS rebinding (`evil.example:<daemon port>`): navigation and same-origin fetch both 403. Framing: `/ui/` in an iframe renders Chromium's error page (XFO DENY).
- Static UI traversal (`trav.py`): ten `..`, encoded, backslash and NUL variants all 404.
- Token exposure (`tok.py`): not in `/status`, `/config`, the process argv, or any file the daemon wrote; uvicorn access log is off (`log_level="warning"`), so `?token=` on `/ws` is not logged.
- Download names: `_safe_download_stem` and `export_filename` restrict to `[A-Za-z0-9._-]`; bundle member names come from sanitised port stems; session `.db` temp files are `mkstemp` (0600) beside the capture.
- Serial input: decoded as ASCII with replacement (`serial_link.py:819`), so no C1 or bidi controls arrive from a board; partial line capped at 4096; `!pd` sids 0-9; regex matching uses `regex` with per-call and per-query budgets.
- WebSocket: the handler drains client frames (receive loop), subscriber cap 256.

## Not covered

- Firefox (not installed) and Excel (Windows): SEC-3 driven in LibreOffice only.
- A second uid for SEC-1; an IPv6 LAN client for SEC-7.
- Windows file ACLs for the data dir and Windows-specific port squatting (class 3 covers exclusivity).
- `mcu plot list` against 1.7 M channels; PlotJuggler UDP output under rotating names.
- Update-check response parsing beyond the UI's href check.

## The two questions

1. Least confident: the SEC-2 rate at a real baud is extrapolated (measured at socket speed, ~52k names/s), and attributing the growth to `_plot_summary` rests on the recurring-name control plus reading, not a heap profile. Re-driving at 115200-equivalent pacing for an hour, with `tracemalloc` on the summary, would settle both.
2. Not yet thought about: other per-key state fed by the wire. A grep of daemon collections found only `_plot_summary` keyed by device data, but the PlotJuggler stream and `mcu plot list` both walk the same channel set and were not driven at 1 M names.
