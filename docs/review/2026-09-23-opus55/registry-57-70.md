# Registry leg, classes 57-70 (2026-09-24)

HEAD `f31ecd995ee2ed193d8d60637b76620ddc880be3` (checked with `git rev-parse HEAD` before the sweeps).
Scratch, probes and full sweep outputs: `~/tt-data/mcuscope-2026-09-24/registry-leg/57-70/`.

## Findings

- **R57-1** LOW `host/mcuscope/webui/can.js:626-628` (`visibleCanIds`, used by `openCanExport` at `:670`).
  - The "CAN ids" prefill of the history export is the union of every port's shown rows, while the export is one port's (`port=chosen(v)`).
  - Two boards, p1 showing id 100 and p2 showing id 200: the default p1 export asks `/can/frames?port=p1&id=100,200`, so p1's frames of id 200 that are not on screen (before a clear, or evicted by the id cap) are exported, and the field names an id p1 never showed. Changing the Port select does not refresh it.
  - Driven: `probe_can_ids2.test.mjs` (the `can_export_port.test.mjs` harness), expected `100`, got `100,200`.
- **R58-1** LOW `host/mcuscope/serial_link.py:893`: docstring says `/status` and `mcu port list` show the rx drop; there is no `mcu port` command (`mcu ports` / `mcu status`). Driven: `mcu ports --help`, click tree walk in `c58.py`.
- **R58-2** LOW `host/mcuscope/serial_link.py:1344`: comment names `mcu port reconnect`; no such command, the reconnect is `POST /ports/{alias}/reconnect` only. Same evidence.
- **R61-1** LOW `host/mcuscope/webui/settings.js:122` (`renderToken`), called at `:781` and `:784` after `openSettings` awaits `/config` and `/devices`.
  - The dialog opens before the answers land and the token field is deliberately left live (`DAEMON_FIELDS` excludes it, comment `:96`), so a token typed or pasted while the load is out (up to `STATUS_TIMEOUT_MS`, 2 s, and the unreachable branch is exactly when a token is being entered) is overwritten with the stored one, and the section then reads clean.
  - Driven: `probe_token_typed.test.mjs`, both the answered and the unreachable branch: expected `typed-secret`, got `''`.
- **R63-1** LOW `host/tests/test_cli_attach.py:130` (and its pair `:118`): the positive control for "a real miss reports the daemon's no such port" answers the detach with 404.
  - The daemon answers an unknown alias on `DELETE /ports/{alias}` with 400 (`server.py:1173-1174`); `cli_client.py:200-204` treats a 404 as a missing route and probes `/status` first, so the control runs a branch real misses never take. Reasoned from the code.
- **R63-2** LOW `host/tests/test_cli_send_verdicts.py:31-33` (`_verdict`), used by `test_assert_names_a_send_that_failed` (`:85-96`).
  - A failed send gets `checked_lines: 5` and no `reason`, and the test asserts `FAIL  5 lines checked`. The daemon returns `verdict(0, ...)` on a failed send before any line is judged (`server.py:3154-3157`), so the asserted output is unreachable.
  - The real output, driven with the daemon's own body (`probe_assert_send.py`): `FAILED  send 'reset': ERR 1 badcmd unknown reset; the window judged no stimulus`, `ok      forbid 'PANIC': never seen`, `FAIL  0 lines checked in 1 ms`. The fixture hid that an unjudged forbid is printed as `ok ... never seen`.
- **R63-3** LOW `host/tests/test_cli_read_scope.py:155-166`: the positive control `tail -n 1` answers `{"lines": [], "truncated": true}`, which no `limit >= 1` query returns (truncated implies a returned row). The note keys on `truncated` alone, so no wrong result; fixture fidelity only. Reasoned.

## Class 57: per-board state keyed by a name unique only within a port

Sweep 1: `grep -n "new Map(\|Object.create(null)" host/mcuscope/webui/*.js`: 50 lines.

- `chrome.js:25` colour store, `:42` `paletteSlots`: exempt, keyed by name by decision (colour follows a name across boards).
- `chrome.js:93` `windowGroups` (selector registry), `:231` `soloShow` over one chart's or the lanes' own keys: comply.
- `freeze.js:12` surfaces by surface name: comply (not device data).
- `can.js:26` `canRows`: complies, key `port|bus|s/x id` (`:140`). `:374` `cells` keyed by the same key; `:551`, `:694` frozen copies of `canRows`; `:654` `byPort` keyed by port: comply.
- `api.js:528`, `:760`, `:761` clear tokens keyed by pane object: comply.
- `exportdlg.js:21`, `:55` dialog fields by option name: comply.
- `timewindow.js:280`, `:322` keyed by port: comply.
- `layout.js:60` titles keyed by chart key (`port|s<sid>`): complies.
- `digital.js:25` lanes `port|name`, `:26` groups `port|group`: comply. `:54` `laneIds` keyed by `stream`, which is the chart key `port|s<sid>` (`plots.js:301`): complies. `:320` solo over lane keys, `:494` keyed by port: comply.
- `state.js:125`, `:126` keyed by alias, `:169` port colour by alias, `:480` command mode by alias: comply.
- `plots.js:30` type table: n/a. `:44` `plotDefs` `port|sid`, `:78`, `:79` chart key: comply. `:202` names within one definition, `:322`, `:332` one line's points, `:366` `seedDef` over one chart key's group (`:403-427`), `:397` `fresh` `port|sid`, `:403` chart key, `:458` one chart's series, `:582` one sample, `:668` `plotChannelMeta` `chartKey|name` (`:656`), `:740`, `:895`, `:1330` per chart: comply.
- `statusbar.js:277`, `:294`, `:331` by alias, `:309` reason table, `:518` by alias: comply.
- `settings.js:66` by section id, `:330` by config path: comply.
- Beyond the grep (`new Set(`, plain objects): `can.js:627` `visibleCanIds` is **R57-1**; `can.js:651`, `digital.js:488-489`, `api.js:361`, `plots.js:481` are per-port sets: comply; the rest are value sets or scratch.

Sweep 2: dicts keyed by `sid`, `name` or `can_id` in `cli*.py`, `server.py`, `pjstream.py` (`grep -nE "\[(sid|name|...)\]|\.get\(...\)|\.setdefault\(|: dict|= \{\}|dict\(\)|defaultdict"`: 83 lines, 70 of them type annotations, request bodies or params dicts with no per-board key).

- `cli_output.py:376` `_pds` and `:381` `_last` keyed by port and `(port, key)`: comply.
- `server.py:1970` `detached_meta` by alias, `:1999` meta looked up in the row's own port: comply. `:2112` `decs` per port (`_plot_export_defs`): complies.
- `server.py:3531` `values` (one line, one port), `:3532` `last` keyed `(port, name)`: comply. `:3575` `bands` by name: exempt, `deadband=` is a user selection by name over the exported names (SPEC 9.2 grammar).
- `server.py:3633` `_decode_map`, per decoder and so per port; `:3674`/`:3690` `decs` per port; `:3710` `(port, sid, name)`; `:3749` per port: comply. `:3766-3779` `labels` by name: exempt, a CSV header has one label per column and without `port=` columns merge boards by SPEC 9.2 (documented at `:3760-3763`).
- `pjstream.py:154` one datagram's channels under the alias: complies.
- `server.py:754` token failures by host, `:1634` bundle stems by port, `:2730` pools by name: comply.

Newest-N priming feeding a per-port store: `serial_link.py:85-95` `learn_stored_plot_defs` (port-scoped, `limit=1000`, desc): complies, one port. `server.py:3735-3747`, `cli.py:813`/`:849`, `webui/api.js:285-295`: page the whole bounded window: comply.

## Class 58: in-product help naming a key, flag or variable nothing reads

Method: `c58.py` extracts from `host/mcuscope/**/*.{py,js,html,css}` (vendor excluded), `README.md`, `host/README.md`, `docs/*.md`, and resolves against `config._KNOWN_KEYS` / `_KNOWN_PORT_KEYS` (with `server.token` treated as unread), the environment names read in code, `daemon.build_parser()` and `sim.build_parser()` flags, and the typer command tree. `c58b.py` checks every `--flag` written after `mcu <cmd> [sub]` against that command's params plus the globals.
Counts: 193 `section.key`, 79 env, 50 `mcuscoped --x`, 7 `mcu-sim --x`, 434 `mcu <cmd>`, 266 `mcu ... --flag` tokens. Every resolved token complies; every unresolved one is ruled below (full lists in `c58-out.txt`, `c58b-out.txt`).

- Env: 0 unresolved; every name is read (`dirs.py:18` covers `MCUSCOPE_{CONFIG,DATA,CACHE}_DIR`).
- `section.key` unresolved, all exempt as code identifiers, not config keys: `server.py` (module name, 25 sites in code and docs), `ports.append/get/list/attach/hold/stop_all/add/size/length/includes/map/push`, `server.run/started/config`, `update.json` (cache file), `update.available/latest/checked_at` (`/status` fields), `server._csv_wide`.
- `server.token` sites: `config.py:402`, `statusbar.js:24`, `SPEC.md:523`, `REVIEW.md:739` say it is ignored: comply. `daemon.py:123,125,429`, `server.py:584,1225,1285` are the runtime `config.server.token` attribute: comply.
- Daemon flags: `sim.py:1247` (`--plot` is mcu-sim's own flag, quoted in its help) complies; `REVIEW_LOG.md:451` `mcuscoped --sim --plot`: exempt, a dated log entry.
- Commands: `serial_link.py:893` **R58-1**, `serial_link.py:1344` **R58-2**. `server.py:1125` ("run mcu on the ..."), `__init__.py:11`, `cli.py:125`, `cli.py:3204`: prose, not command names. `IDEAS.md:37-311` (`since`, `port dtr`, `doctor`, `poll`, `plot tail`, `run`, `session pin`, `stats`, `snapshot`, `boot`, `session diff`, `conformance`, `report`): exempt, proposals.
- Flags: `IDEAS.md` `assert --channel/--min/--max/--every/--max-gap-ms/--from-port/--to-port/--match/--max-ms`, `plot tail --names`: exempt, proposals. `can dump --decode` at `DBC_DECODING.md:14`, `IDEAS.md:18`, `SPEC.md:1949`: exempt, SPEC 10 "do not build in v1". `cli.py:3059` `--reg`: false positive (the span started at `mcu i2c scan`); `mcu i2c rd --help` lists `--reg`.

Sweep imprecision: the registry's token list needs the command regex to allow digits (`mcu i2c` read as `mcu i`), typer groups are not `click.Group` (test `hasattr(cmd, "commands")`), and `section.key` matches every `server.py` mention; restrict it to backticked or `[section]`-context tokens. It also never checked `mcu <cmd> --flag` mentions; `c58b.py` does.

## Class 59: a display rule defeating the hidden attribute

Sweep: `grep -n "hidden\]" host/mcuscope/webui/style.css`: 2 lines, `:39` (comment) and `:40` the global `[hidden] { display: none !important; }`: comply.
`grep -n "!important" style.css`: 5 lines; `:441` is `display: none !important` (complies), `:168`, `:354`, `:440`, `:457` are not display rules.
Inline `style.display`: 5 sites (`statusbar.js:665`, `:668`, `:672`; `settings.js:521`, `:523`) plus 3 markup defaults (`index.html:144`, `:147`, `:158`); none of `devCustom`, `bindRow`, `baudCustom` is ever toggled with `hidden`: comply. No `setProperty("display", ..., "important")` anywhere.
Owed in a real browser: nothing new; the rule is standard CSS and this sweep is textual.

## Class 60: a constraint judged on the raw value, the stored value normalised after

Sweep: constrained `Field(` in server.py (38 lines) against `grep -n "\.strip()\|\.lower()" server.py` (17 lines).

- Transforms on constrained fields: `:319` `SessionBody.name` validator strips then refuses blank: complies; `:1496` strips again, no-op. `:1312` `db_path.strip()`: only `max_length`, and empty means the default: complies. `:1379` `dest.strip()` after `pjstream.parse_dest`, which strips and refuses empty itself: complies. `:1428-1429` device/serial strip to None, then "device or serial_number required": complies. `ConfigServerBody.host` goes through `check_host` (strip, refuse blank): complies.
- The other strip/lower sites (`:597`, `:598`, `:610`, `:643`, `:657`, `:699`, `:795-802`) are header parsing: exempt, no model constraint.
- Web UI: every required check follows its trim (`statusbar.js:229-230`, `:690-691`, `settings.js:609-610`, `:695-697`, `cmdbar.js:177-178`): comply.

## Class 61: a re-render that rewrites a field the user is typing in

Sweep: `grep -n "\.value = " host/mcuscope/webui/*.js`: 52 lines (a wider grep for `.value=`, `selectedIndex`, `defaultValue` adds nothing).

- Option elements (not fields): `cmdbar.js:61`, `exportdlg.js:79`, `:162`, `:170`, `state.js:445`, `statusbar.js:642`, `:648`, `:652`, `terminal.js:643`, `settings.js:482`, `:488`, `:495`: exempt.
- Written on open or reset: `exportdlg.js:88` (buildOptions), `:101-102` (`render`, from open and Reset only, see `:46`, `:115`), `statusbar.js:218-219`, `:574-576`, `settings.js:141-160` (after the fields' hold lifts), `:509-547` (row build on open or after an unchanged save, `:678`), `terminal.js:705`: comply.
- Written on the user's own action: `cmdbar.js:194`, `:216`, `:247`, `:254`, `:255`, `:272` (guarded by `=== sent`), `terminal.js:714`, `:898` (CAN id click, registry exemption), `plots.js:528`, `chrome.js:68`: comply.
- Guarded late answers: `settings.js:228`, `:245` (only while the field still reads what was sent), `statusbar.js:613` (`aliasTyped`), `exportdlg.js:164`, `:182` (the select is emptied until the fill lands): comply.
- Poll writers that write the value the control already holds: `cmdbar.js:65`, `:120`, `terminal.js:647`: comply.
- `settings.js:122` `renderToken`: **R61-1**.

## Class 62: a control's value written before the control can hold it

Sweep: `grep -n 'createElement("option")\|fillEolOptions' host/mcuscope/webui/*.js`: 17 lines, 8 JS-filled selects.

- `cmdPort`: filled and written in `populateCmdPort` (`cmdbar.js:59-65`): complies.
- `cmdEol`: `fillEolOptions` (`cmdbar.js:284`) runs before `populateCmdPort`'s `syncCmdEol`: complies.
- Export option selects: options appended (`exportdlg.js:76-82`) before `input.value` (`:88`): complies.
- `expSession`: emptied, then filled before `sel.value` (`:161-182`): complies.
- `devSel` (attach): no value writer; the first option is the default: complies.
- `attachEol`: filled at module load (`statusbar.js:733`), written only on open (`:576`): complies.
- Pane port select: filled before `sel.value = cur` (`terminal.js:641-647`): complies.
- Settings row `devSel` (`settings.js:480-498`) and `eolSel` (`:546-547`): filled first: comply.

## Class 63: a test fixture in a state the producer cannot reach

"New" was taken mechanically: canned-response lines at HEAD with no identical line at the round's base `6e4f6f7` (`4e618e6^`), multiset diff, since `c8e2fd4` moved every test file.
Pattern 1 (`recorder\(|MockTransport|json: async \(\) =>|ok\(\{|res\(ok|status: [0-9]{3}`): 36 new lines. Pattern 2 (`httpx\.Response\(|run_mcu_canned\(|recorder\(monkeypatch`): 116 new lines. Each file's bodies were traced to the daemon branch:

- `test_cli_attach.py:19-20`, `:38`: the `device`/`serial_number` pairs are what `SerialPort.status()` reports (`serial_link.py:1290`): comply. `:118`/`:130` **R63-1**. `:160` 400: complies. `:168` 404 "Not Found" (an old daemon's unknown route): complies.
- `test_cli_can_dump.py:174` 500: complies (the unhandled-error handler).
- `test_cli_closed_stdio.py:63`, `test_cli_prompts.py:28` purge body, `test_cli_prompts.py:47-48` `lines_deleted`, sessions list: match `server.py:1549`, `:1773-1794`: comply.
- `test_cli_daemonctl.py:293`, `test_cli_output_rows.py:52`, `test_scaffold.py:137`, `test_port_health.py:94-146`, `statusbar_*` `/status` bodies: subsets of `/status` with reachable values: comply.
- `test_cli_follow.py:126`, `test_cli_follow_frames.py:97-139`: `/wait` without port or send watches every port (`server.py:2607`), so a match on port b is reachable: comply.
- `test_cli_read_scope.py:35` 400 `no such port` (`_unknown_port`), `:36-199`: comply except `:155-166` **R63-3**.
- `test_cli_send_verdicts.py`: `/wait` with an ERR or timeout `cmd_result` is reachable (`send_command` returns it, the wait continues, `server.py:2660-2711`): comply. `_verdict("fail", cmd_result=...)` **R63-2**. `_verdict("empty")` with 0 lines: complies.
- `test_cli_small_refusals.py`: `/marker` `{"line_id"}`, `/send` and `/break` `{"ok": true}`, `/cmd` bodies, 400 texts: comply.
- `test_cli_transport_timeouts.py:39` `{"status": "timeout"}`: complies (`server.py:2697`).
- `test_port_column_stored.py:201` `/ports` with `stored`: complies (`server.py:1145`).
- JS: `api_ws_gap`, `plots_seed_paused` (sid is TEXT in the store, so `"0"` is right; the backfill omits the `!ps` rows the seed dedups anyway), `statusbar_can_now`, `terminal_limits` (`truncated` computed from the limit): comply.
- `cmdbar_raw_line.test.mjs:11`, `cmdbar_sole.test.mjs:14`: `/send` answered with a `/cmd`-shaped body; exempt, no assertion reads the reply. `cmdbar_refusal_inflight.test.mjs:13`: complies.
- `exportdlg_pending_close`, `exportdlg_preflight_busy`, `settings_pj_restart` (a `restart_required: false` for a changed field is reachable when the running value already equals it), `settings_user_text`, `state_download_navigate`, `state_download_wait` (BUSY text is `server.py:2725` verbatim): comply.
- `statusbar_session_dialog.test.mjs:93`: the 422 text omits the ` (got '...')` suffix `_validation_error` always adds; exempt, the UI shows the text verbatim and the test asserts no wording.

Sweep imprecision: "a new test" has no mechanical definition after a test reorganisation, and the registry's grep misses `httpx.Response(`, `run_mcu_canned(` handlers and bodies held in constants. Use the multiset diff against the round base with both patterns above.

## Class 64: a float parameter that accepts inf and nan

Sweep: `grep -n "float" host/mcuscope/server.py`: 40 lines.

- Inputs: `:295` `PurgeBody.before_ts`, checked `:1759`: complies. `since_ts`/`until_ts` at `:1843-1844` (`/lines`, checked `:1852`), `:1875-1876` (`/lines/export`, `:1888`), `:1924-1925` (`/can/frames`, `:1934`), `:2050-2051` (`/plot/export`, `:2071`): comply via `_check_window` (`:3353-3358`).
- Not inputs, exempt: `:116`, `:119`, `:208`, `:1758`, `:3356`, `:3588` (comments); `:754-771` (monotonic clock), `:2332`, `:2381`, `:2503`, `:2561`, `:3054`, `:3252-3258`, `:3369-3381`, `:3517-3532`, `:3569-3575`, `:3698-3710` (internal signatures and locals). Deadband values go through `parse_plot_value`, which refuses non-finite text.

## Class 65: a stop signal that misses a receiver born or lagging after it

Sweep: `grep -n "subscribe(\|is None\|None in" server.py store.py`: 99 lines.

- `store.py:767-793` `stop_subscribers`: sets closed, drops the oldest from a full queue (counted) to seat the sentinel: complies. `store.py:1279-1284` `subscribe` refuses once closed; `:1328` fan-out stops once closed: comply. `:1299` unsubscribe: n/a.
- `server.py:2200-2205` `/ws`: refused with 1001 once closed, 1013 at the cap; `:2236` the pump hands out rows ahead of the sentinel and stops; `:2285` unsubscribe: comply.
- `server.py:2477` `CaptureWatch.open` (`/wait`, `/assert`), refusal answered 503 at `:2636` and `:3138`; `:2517`, `:2531` `next_batch` returns rows ahead of the sentinel then raises `CaptureStopped`; `:2481` close: comply.
- Exempt, `is None` tests on values that are not subscriber items: server.py `174, 382, 636, 823, 1019, 1034, 1122, 1129, 1184, 1340, 1515, 1538, 1545, 1565, 1607, 1619, 1643, 1655, 1682, 1754, 1771, 1907, 1994, 2192, 2441, 2582, 2616, 2694, 2738, 2771, 3041, 3044, 3047, 3057, 3187, 3193, 3276, 3279, 3286, 3287, 3293, 3294, 3311, 3327, 3330, 3342, 3382, 3393, 3409, 3591, 3650, 3656, 3660, 3701` (54); store.py `368, 446, 479, 482, 485, 684, 710, 813, 826, 834, 854, 886, 912, 1271, 1333, 1362, 1516, 1570, 1602, 1705, 1710, 1728, 1748, 1765, 1766, 1854, 1887, 1990, 2341, 2356, 2657, 2864, 2865, 2904, 3071, 3073` (36; `826-912` are the writer queue's own sentinel, a single consumer).

Sweep imprecision: `is None` returns 89 lines of noise; `grep -n "subscribe(\|stop_subscribers\|subscribers_closed\|None in rows" host/mcuscope/*.py` finds the same 10 sites alone.

## Class 66: loop-owned state mutated from a signal handler

Sweep: `grep -rn "signal\.signal\|handle_exit\|add_signal_handler\|SetConsoleCtrlHandler" host/mcuscope/*.py`: 10 lines.

- `daemon.py:277-284` `Server.handle_exit`: schedules `stop_subscribers` with `call_soon_threadsafe`: complies. uvicorn 0.52 installs it inside `serve()` (`capture_signals`), so a loop is always running when it fires.
- `daemon.py:370-372` `_handler`: pid record release and re-raise after uvicorn restored the handlers; touches no loop state: complies. `:374-375` `_hangup` raises SIGTERM: complies. `:385` registration, `:271`, `:484` comments: n/a.
- `_stdio.py:83-97` Windows ctrl handler (a thread, not a Python signal handler): `console_close_hook` sets one float on the uvicorn config (`daemon.py:306`) and interrupts the main thread: complies. `:103-104` registration: n/a.

Sweep imprecision: `SetConsoleCtrlHandler` callbacks run on their own thread and belong in this sweep; the registry's grep does not name them.

## Class 67: an internal freeze read as a caller-supplied bound

Sweep: `grep -n "max_id()" host/mcuscope/server.py`: 6 lines.

- `:3295` `_resolve_window` freeze: taken after `floor_ts` is anchored at the caller's bound (`:3279`): complies.
- `:1542` session delete, `:1622` bundle span, `:1781`, `:1784` purge: no relative parameter follows: comply.
- `:2476` `CaptureWatch` watermark: the window is timed on the loop clock, not by id: complies.
- Beyond the grep: `cli.py:691-708` `_pin_ceiling` pins `id_to` client-side, but only with `until_ts`, and `--last-ms` is already an absolute `since_ts` (`_absolute_window`, `:724`): complies.

Sweep imprecision: add `_pin_ceiling` and `id_ceiling` (client and server freezes not spelled `max_id()`).

## Class 68: a refusal judged on state primed before the window it governs

Sweep: `grep -n "primed\|_plot_export_defs\|declared_kinds" host/mcuscope/server.py`: 13 lines.

- `:2117-2129` deadband-on-label refusal: judged over primed decoders plus `_def_decoders` of the in-window `!pd` rows: complies.
- `:1663` bundle: primes decoders, refuses nothing: n/a. `:3622` `_renders_as_label`, `:3719-3752`, `:3756`, `:3767`: the helpers: n/a.
- Other refusals on that path: the wide single-stream refusal reads the window (`export_sids_safe`), the unknown-name refusal reads the capture summary by design (`:2101-2109`): comply. No client-side refusal reads seeded state (`deadband` in `exportdlg.js`, `cli.py:2482` is a flag-pairing check).

## Class 69: an output target opened before the peer accepted the request

Sweep: `grep -n 'open(out\|os.remove\|unlink' host/mcuscope/cli*.py`: 5 lines.

- `cli_client.py:254` `download`: opens after `status_code >= 400` fails out (`:250-252`); `started` guards the removal: complies.
- `cli_output.py:179` `remove_partial`: removes only an `lstat` regular file, a symlink's target by stated design (the open truncated it): complies.
- `cli.py:2815`, `cli_daemonctl.py:227`, `:256`: pid record and its temp file, not a `-o` target: exempt.
- Beyond the grep: `cli.py:1803` `_OutFile` opens on first write, used by `_stream_export` (`:1862`, `/lines/export`, `/can/frames`, `/plot/export`) and the paged log export (`:1967`); a refusal on the first page raises before any write and `discard()` removes nothing: comply.

Sweep imprecision: add `open(self.path` and `_OutFile(`; the lazy open is where every text export now writes.

## Class 70: one status code for several causes, mapped to one

Sweep: `grep -n "status_code ==\|status ===\|\.code ==" host/mcuscope/cli*.py host/mcuscope/webui/*.js`: 7 lines; widened to `status in`, `400 <=`, `>= 400` for 5 more.

- `cli_client.py:194` 503: keyed on the body prefix `daemon is shutting down`, which matches `_SHUTDOWN_MSG`, `_CMD_SHUTDOWN_MSG` and `SUBSCRIBERS_CLOSED_MSG` (all shutdown, exit 3), not the cap or export-pool 503s (exit 1): complies.
- `cli_client.py:200` 404: the daemon emits none of its own (no 404 in server.py; unknown routes only): complies.
- `cli.py:1299` ws 1008: emitted after accept only for `no such port` (`server.py:2196`, with a reason); the guards' 1008 closes are sent before accept and arrive as HTTP 403 (driven: `probe_ws_close_before_accept.py`, `InvalidStatus 403`): complies.
- `cli.py:1305` ws 1013: after accept only for the subscriber cap (`server.py:2205`); the token rate-limit 1013 (`:855`) is pre-accept, so a 403: complies (same probe).
- `cli.py:1315` `status in (502, 504)`: gateway codes the daemon never emits: complies. `cli.py:2312` 4xx: every 4xx is a refusal (exit 1): complies. `cli_client.py:218`, `:250`, `:286` `>= 400`: generic: comply.
- `state.js:80` 401: emitted only by the token guard (`server.py:843`): complies.
- `cmdbar.js:224`, `:226`: the `/cmd` body's `status` field, not an HTTP code: exempt.

Sweep imprecision: the grep misses `status in (...)` and range checks (`cli.py:1315`, `:2312`), and a ws close code sent before accept never reaches a client as that code.

## Owed

- Windows: nothing in 57-70 is platform-gated except the ctrl-handler path of class 66 (`_stdio.py:83-97`), ruled by reading only.
- Browser: none needed for these rulings; R57-1 and R61-1 were driven on the DOM stub, which models neither select semantics nor focus, so a real-browser repro of R61-1 (paste a token into Settings against a stalled daemon) is the owner-visible check.

## The two questions

1. **Least confident:** class 63's scope.
   - "New" is a multiset line diff against `6e4f6f7`, so a fixture whose line was untouched by the move but whose surrounding test was rewritten was not traced.
   - Bodies held in constants were traced only in the files the diff named.
   - Rechecked by widening the grep to a second pattern (116 more lines), which found R63-1 and R63-3.
   - R63-1 and R63-3 are reasoned; R63-2 was driven.
2. **What we have not checked:**
   - Class 57's priming floor is `max_id() - PLOT_DEF_LOOKBACK` over every port (`serial_link.py:89`). A detached board's definitions age out of `/plot/channels` once other boards write 20000 rows. SPEC 1819 bounds it "within the priming lookback", so it is by design, but the lookback is global ids, not that board's time.
   - Outside these classes: `settings.js:609` drops a port row whose alias was cleared, deleting that port from the config on save with no word. The comment covers only the untouched "+ port" row. Reasoned only.
