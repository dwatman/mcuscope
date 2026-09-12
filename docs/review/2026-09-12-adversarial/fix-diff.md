# Fix-diff leg, 2026-09-12 adversarial round

HEAD `c15b7c6`, 63 entries in `git status --short` when the leg began and 65 at its end (49 modified plus the round's new test files and this directory; the two added meanwhile are the orchestrator's).
`git diff HEAD --stat` = 49 files, 2288 insertions, 365 deletions, identical at both ends.
Every drive below ran against the working tree as found; the tree was byte-identical before and after this leg (`git diff --stat` compared at both ends).
Scratch, drivers and logs: `/tmp/rev-2026-09-12/fixdiff/`.

## Findings

| id | sev | where | claim | how verified | recommended fix |
|---|---|---|---|---|---|
| F1 | **HIGH** | `server.py:1935,1948` + `store.py:612` `_stop_subscribers`, against `daemon.py:385` `timeout_graceful_shutdown` | Improvement 7's shutdown 503 **never fires on a real stop**. `store.stop()` runs in the lifespan's `finally`, which uvicorn only reaches after its graceful wait for in-flight requests has expired, so the parked `/wait` handler is cancelled first and the client still gets `500 Internal Server Error` at exit 1 - the exact behaviour the fix's own test docstring says it replaces. The test drives `store.stop()` directly on a live app, an ordering that cannot occur in production. | Driven twice on a throwaway stack (`mcuscoped --sim --config /tmp/rev-2026-09-12/fixdiff/stack/cfg.toml --port 18621`), both documented stop paths: SIGTERM to the daemon pid, and `mcu daemon stop` (POST /shutdown). Both: `mcu wait --timeout 20000` returned `error: Internal Server Error`, **rc=1**. The daemon log carries `asyncio.exceptions.CancelledError: Task cancelled, timeout graceful shutdown exceeded` at `server.py:2185`, the `q.get()` the sentinel was meant to wake. | Push the sentinel at the *start* of shutdown, not at store teardown: call `store._stop_subscribers()` (made public) from the `/shutdown` handler and the signal path before uvicorn's graceful wait begins, leaving `store.stop()` to do the rest. Then re-drive through a real process stop, not through `store.stop()` on a serving app. |
| F2 | MED | `cli.py:1793` (`format`), `2103` (`port`), `2105` (`decode`), `2107` (`changes`), `2109` (`deadband`) | C3 was closed on `--from`/`--to` only; the class-53 sweep finds five more parameters the CLI moved onto the query that a pre-0.4.0 daemon does not declare, each silent. At `v0.3.0`, `/plot/export` declares exactly `{format, id_to, last_ms, names, session}` and `/can/frames` declares no `format`. So against that daemon `mcu plot export --deadband`/`--changes`/`--decode`/`-p` export the unfiltered, undecoded whole at exit 0, and `mcu can dump --csv -o f.csv` writes a JSON document into the `.csv`. | `git show v0.3.0:host/mcuscope/server.py`, parameter sets extracted per endpoint (listed in the class 53 sweep below). Consequence driven on the live 0.4.0 daemon by using a name it does not declare, the same mechanism: `deadband=` -> 2 lines, `deadbandX=` -> 5219; `port=nosuchboard` -> 0 lines, `portX=` -> 5222; `format=csv` -> CSV header, `formatX=csv` -> `{"frames":[...`. | Extend `_require_clock_bounds_daemon` into one `_require_daemon(s, min_version, what)` and call it from `plot export` when any of `--decode/--changes/--deadband/-p` is given and from `can dump` when `--csv` is, or gate the whole 0.4.0 export surface once per process. |
| F3 | MED | `state.js:305-320` `streamable` / `downloadPath` | The no-token navigation branch is keyed on "no token is set" where the property that matters is "this daemon needs no token". After a 401 whose prompt the user cancelled (`tokenGaveUp`, `hooks.authFailed` already fired), every streaming export still goes out as a plain `<a download>`: no `Authorization` header, no fetch, `downloadPath` returns null (success), the dialog closes, and the browser saves the daemon's 401 body under `lines.csv`. This is the batch's own design-call 1 one step worse: not a possible 4xx from a bad range, but a certain one for every export. | Driven, `/tmp/rev-2026-09-12/fixdiff/tokendrive.mjs` on the DOM stub: `api("GET","/status")` 401s with `prompt` returning null, then `downloadPath("/lines/export?format=csv", ...)` reports `navigated to: /lines/export?format=csv | fetch used: false | returned err: null`. | `if (!authToken && !tokenGaveUp && streamable(path))`, or record from the first unauthenticated 200 that this daemon needs no token and key the branch on that. |
| F4 | LOW (class 55) | `server.py:2947` `_renders_as_label` | The deadband refusal is keyed on `channel_meta()[name]["kind"]`, but the path it guards (`_render`) keys on the `(name, sid)` entry of `_decode_map`: a row whose `sid` differs from the last declaration of that name renders **numerically** (`num` set, the band would apply) while the refusal still fires. Two streams declaring one name (SPEC 2.5, last declaration wins) therefore get a false 400 saying the channel "renders as a label". Pre-existing for `enum`, widened to `bit` by D7; SPEC 9.2's new deadband paragraph inherits the same wording. | Driven: `PlotDecoder` learning `!pd 0 mode:u1` then `!pd 1 mode:u1:=0=IDLE,1=RUN` gives `channel_meta kind: enum sid: 1`; `_render({"name":"mode","sid":"0","value":7.0}, _decode_map(dec))` gives `cell='7.0' num=7.0`, and `_renders_as_label(dec,"mode")` is `True`. | Key the refusal on the same map the render path uses: refuse only when every sid selected for that name has labels, i.e. test `_decode_map(dec)[name][2] is not None` per sid rather than the name's last-declared kind. |
| F5 | nit (class 9) | `cli.py:355` `_derive_alias(target)` | `mcu attach --serial SN` derives the alias from the serial number, which is arbitrary USB text, so a serial outside `ALIAS_RE` is refused naming `alias` - an option the user never typed. Same shape as C8, which this round fixed on `log export --csv`. | Driven: `mcu attach --serial "AB:CD"` -> rc 1, `error: alias: String should match pattern '^[A-Za-z0-9][A-Za-z0-9_.-]{0,31}$' (got 'AB:CD')`. The happy path is fine: `--serial NOSUCHSERIAL123` attaches and reports `(connecting; ...)`. | Sanitise the derived alias (drop characters outside the grammar), or refuse client-side with "cannot derive an alias from serial 'AB:CD'; pass --alias". |

Not findings, but for the owner's eye:

- **The `/lines/export` CSV now has no formula guard at all.** D4's fix exempts `raw` and `dir`, and `raw` is the only device-controlled cell in that file (`port`, `chan` and `dir` are the daemon's own vocabularies), so a captured line `=cmd|'/c calc'!A1` reaches the CSV unprefixed. That is the deliberate trade (faithfulness over spreadsheet hardening) and SPEC 723 now states it; it is only worth naming because SPEC said nothing at HEAD, so the guard's scope was narrowed and documented in one move.
- **`_effective_bounds` now runs SQLite on the event loop.** `store._window_floor(last_ms, id_to)` executes a primary-key seek on the writer connection from the handler where `time.time()` used to be (class 1). One seek, measured by the batch as free, and `sweep_tick` already touches `_conn` the same way, so it is consistent with the module; noted so a later round does not read it as new.
- **The CLI batch report's last "Not done" bullet is stale.** It says `-o -` is refused on `session export` only; the follow-up section of the same report, and the code, refuse it on all four `-o` commands. Verified below (seam D).

## Revert re-verifications, re-done by this leg

Method for each: `cp` the file aside, replace exactly one anchor (anchor count checked before any write, driver `/tmp/rev-2026-09-12/fixdiff/revert.py`), run the named test file, restore from the copy.
`git diff --stat` was identical before and after the whole set (49 files, 2288 insertions, 365 deletions).

| # | batch | reverted | test file | result |
|---|---|---|---|---|
| R1 | daemon | D1: `+cf.can_id IN (...)` back to `cf.can_id IN (...)` | `test_daemon_r2026_09_12_store.py` | caught, 1 failed (`test_can_id_list_keeps_driving_from_the_frame_table`) |
| R2 | daemon | bundle span: `id_to=hi` back to `id_to=session["end_id"]` | `test_daemon_r2026_09_12_bundle.py` | caught, 1 failed (`test_an_open_sessions_members_all_cover_the_same_span`) |
| R3 | daemon | bundle `async with store._sweep_lock:` -> `if True:` | `test_daemon_r2026_09_12_bundle.py` | caught, 1 failed (`test_a_purge_of_the_span_waits_for_a_bundle_in_progress`) |
| R4 | daemon | store CR/LF fold disarmed (`if False:`) | `test_daemon_r2026_09_12_server.py` | caught, 1 failed (`test_a_multi_line_marker_is_stored_as_one_line`) |
| R5 | daemon | `_check_unknown(data)` call removed | `test_daemon_r2026_09_12_config.py` | caught, 2 failed |
| R6 | daemon | `self._reclaim_backlog()` removed from `sweep_tick` | `test_daemon_r2026_09_12_store.py` | caught, 3 failed |
| R7 | daemon | shutdown sentinel not raised (`if None in rows:` -> `if False:`) | `test_daemon_r2026_09_12_server.py` | caught, 2 failed (both 503 tests) |
| R8 | cli | C3 version refusal disarmed (`if False and is_newer(...)`) | `test_cli_r2026_09_12.py` | caught, 5 failed |
| R9 | sim | `!pd 0 ... ramp:u2*0.1:mA ftest:f4:degC` back to the old literal | `test_sim.py -k units` | caught, 1 failed |
| R10 | webui-a | W1: pane export comma-joins `chan` again | `export_paused_window.test.mjs` | caught, `# fail 2` |
| R11 | webui-a | P1: drag pauses only the chart dragged (`setChartPaused` for `pauseAll`) | `plots_zoom.test.mjs` | caught, `# fail 3` |
| R12 | webui-b | P12: `watermark: () => null` | `can_freeze_surface.test.mjs` | caught, `# fail 1` |

**12 re-done, 12 caught, 0 survivors.**
The batches' own counts (17 CLI, 27 daemon, 23 webui-a, 27+3 webui-b) are consistent with this sample.
Note for whoever re-runs these: `node --test FILE | tail -n` reports *tail's* exit status, so the first pass of R10-R12 read as "survived"; the counts above come from `# pass`/`# fail` in the output, not from `$?`.

## Cross-batch seams, each driven

**A. CLI 503 -> exit 3 against the daemon's 503 on shutdown. FAILS, see F1.**

```
mcuscoped --sim --config /tmp/rev-2026-09-12/fixdiff/stack/cfg.toml --port 18621   # db_path in the toml
mcu --url http://127.0.0.1:18621 wait --match '^ZZZ_never_matches' --timeout 20000 &
kill -TERM <daemon pid>        # and, second run: mcu --url ... daemon stop
```

Both runs: stderr `error: Internal Server Error`, **rc=1**, with `CancelledError: Task cancelled, timeout graceful shutdown exceeded` in the daemon log.
The CLI half is correct in isolation: `cli_client.fail` maps 503 to exit 3 for every endpoint, and `test_a_503_from_the_daemon_is_unreachable_not_an_error` pins it.
The daemon half never produces the 503 outside its own unit test.
Ruled in passing: the two pre-existing 503s (`server.py:2296`, `2578`, `StoreError` from `watch.open()`) are the same family ("the capture is not there"), so the blanket mapping is coherent.

**B. `filterPaneTo` exported by terminal.js, called from can.js/app.js. Green.**

`node --test tests/webui_js/terminal_filter_pane.test.mjs tests/webui_js/can_logic.test.mjs` -> `# pass 29, # fail 0`.
Wiring read end to end: `can.js` exports `setPaneFilter`, `app.js` imports it with a namespace import of terminal.js and passes `terminal.filterPaneTo` behind a `typeof` guard, `can.js fillCanId` calls `filterPaneToId(e)` from both a `click` listener and `makeSpanButton`.
`makeSpanButton` (digital.js:183) binds `onkeydown` only, so the mouse path fires once, not twice.
Gap, not a defect: no test loads `app.js`, so the hand-off itself is pinned only by the two halves agreeing.

**C. `id_to=0` (daemon `ge=0`) against `exportrange.test.mjs:112`'s `id_to=0` expectation. Consistent.**

Against the live stack: `/lines?limit=5&id_to=0` -> 200 `{"lines":[]}`, `/lines/export?id_to=0` -> 200 empty, `/plot/export?names=ramp&id_to=0` -> 200 header only, `/can/frames?format=csv&id_to=0` -> 200 header only, and `/lines?limit=5&id_to=1` -> 200 with row 1.
`exportrange.params()` sends `id_to` whenever `watermark != null`, so 0 survives; the test's pin and the daemon now agree.
Ruled: `cli.py:576`'s backwards paging (`params["id_to"] = oldest - 1`) is guarded by `oldest <= 1` and never sends 0, so the CLI is unaffected either way.

**D. `_refuse_stdout_token` on every export command. Complete.**

Four commands declare `-o` (`cli.py:1310` session export, `1555` log export, `1751` can dump, `2061` plot export) and all four call `_refuse_stdout_token` (`1324`, `1574`, `1765`, `2085`).
SPEC 4's export paragraph states it for the same four.
The report bullet claiming otherwise is stale.

**E. The `!pd` string changes against everything that quotes them.**

`grep -rn 'tri:s2\|ramp:u2\|ftest' host/tests firmware docs README.md`: the only executable site holding the old literal is `host/tests/test_protocol.py:403`, which passes its own literal to `parse_plot_def` as a grammar case and asserts `ramp` has no scale or unit by construction, so it is independent of the sim.
`host/tests/webui_js/api_plot_def_seed.test.mjs:29` and `docs/SPEC.md:1358` carry the new literal.
`host/tests/webui_js/api_plot_seed.test.mjs:67` uses an unrelated definition of its own.
The remaining hits are this round's and earlier rounds' review documents, which record what was true when they were written.

## Sweep: class 53, a bound sent as a query parameter the peer may not declare

Enumeration: `params[...] =` plus dict-literal entries in `cli.py`/`cli_client.py` = **37 sites**; `p.set(` / `p.append(` / `q.set(` / `q.append(` / `searchParams` in `webui/*.js` = **30 sites**. Total **67**, all ruled below.
The reference peer is `v0.3.0` (the newest release older than the current SPEC); its declared parameter sets, from `git show v0.3.0:host/mcuscope/server.py`:

- `/lines`: chan, id_to, last_ms, limit, match, order, port, session, since_id, **since_ts**
- `/can/frames`: bus, id, id_to, last_ms, limit, port, session, since_id
- `/plot/series`: decimate, id_to, last_ms, limit, name, port, session, since_id
- `/plot/export`: format, id_to, last_ms, names, session
- `/lines/export`: **absent**

| sites | parameter -> endpoint | verdict |
|---|---|---|
| cli 2105, 2107, 2109 | `decode`, `changes`, `deadband` -> /plot/export | **violates** - undeclared at v0.3.0, dropped silently, the export comes back whole and undecoded at exit 0 (F2) |
| cli 2103 | `port` -> /plot/export | **violates** - undeclared at v0.3.0; two boards' identically named channels merge, which is the hazard the line's own comment cites |
| cli 1793 | `format=csv` -> /can/frames | **violates** - undeclared at v0.3.0; the JSON body is written into the `.csv` (F2) |
| cli 546, 548 | `since_ts`, `until_ts` -> /lines | complies - `until_ts` is version-gated by `_require_clock_bounds_daemon` (C3); `since_ts` predates v0.1.0 |
| cli 1789, 1791 | `since_ts`, `until_ts` -> /can/frames | complies - both reachable only from `--from`/`--to`, so C3's gate covers them |
| cli 2097, 2099 | `since_ts`, `until_ts` -> /plot/export | complies - same gate |
| cli 532, 566, 1824 | `limit` -> /lines, /can/frames | complies - declared since v0.1.0 |
| cli 534, 1779, 1826 | `port` -> /lines, /can/frames | complies - declared |
| cli 536, 538 | `chan`, `match` -> /lines | complies - declared |
| cli 540, 1787, 2095 | `last_ms` -> /lines, /can/frames, /plot/export | complies - declared on all three |
| cli 542, 590 | `since_id` -> /lines | complies - declared |
| cli 544, 1781, 2101 | `session` -> /lines, /can/frames, /plot/export | complies - declared since v0.1.0 |
| cli 550, 576 | `id_to` -> /lines | complies - declared |
| cli 1783, 1828 | `id` -> /can/frames | complies - `id` is declared; the comma-list *form* is 0.4.0, and an old daemon refuses it with `bad can id`, which is loud |
| cli 1785, 1830 | `bus` -> /can/frames | complies - declared |
| cli 2093 | `names`, `format` -> /plot/export | complies - declared |
| cli 1592 | `format` -> /lines/export | exempt - the endpoint does not exist before 0.4.0, so the call 404s loudly |
| webui, all 30 (api.js 326, 522; terminal.js 439-442, 513-519; exportrange.js 53-60; plots.js 1043-1049; digital.js 340-346; can.js 515-516) | every parameter | exempt - the UI is served by the daemon it queries, so its parameter set is that daemon's own build. The one residual is a tab left open across a daemon restart at another version; the page reloads on reconnect, and nothing here is worth a version gate |

## Sweep: class 54, a wire shape built by hand at two sibling sites

Every parameter with more than one builder, from the same 67 sites.

| parameter | builders | verdict |
|---|---|---|
| `chan` | cli.py:536 (one scalar), terminal.js:441 and 517 (repeated `append`) | **violates** - the forms differ. The daemon takes a repeated parameter (`list[Chan]`); the CLI can express exactly one channel, and the natural comma spelling reaches the user as a wire message: `mcu lines --chan debug,event` -> `error: chan.0: Input should be 'debug', 'cmd', ... (got 'debug,event')`, rc 1. SPEC 4 does spell `--chan C` singular, so this is a documented limit with an undocumented failure mode. Fix: make `--chan` repeatable (or split on commas) and send the repeated form |
| `port` | cli 534, 1779, 1826, 2103; terminal.js 440, 513; api.js 326 | complies - one scalar alias everywhere |
| `match` | cli 538; terminal.js 442, 518 | complies - raw pattern, no encoding on either side |
| `session` | cli 544, 1781, 2101; exportrange.js 53 | complies - id or name as a string |
| `since_ts` / `until_ts` | cli 546, 548, 1789, 1791, 2097, 2099; exportrange.js 55, 56 | complies - epoch seconds on both sides, no ISO form anywhere |
| `last_ms` | cli 540, 1787, 2095; exportrange.js 58 | complies - integer milliseconds (the web UI rounds) |
| `id_to` | cli 550, 576; exportrange.js 60 | complies - integer; JS stringifies, which is the same wire byte |
| `since_id` | cli 542, 590; terminal.js 439 | complies |
| `id` (CAN) | cli 1783 (`",".join`), cli 1828 (joined at the call site, `1803`), can.js 516 (comma text from the field) | complies - one comma-joined form; the follow path joins before the call, so the repeated-parameter trap is avoided |
| `names` | cli 2093 (`--names` passed through), plots.js 1043, digital.js 340 (`join(",")`) | complies - comma-joined on both sides |
| `format` | cli 1592, 1793, 2093; plots.js 1044, digital.js 341, can.js 515, terminal.js 519 | complies - each site uses its own endpoint's vocabulary, none overlap |
| `decode` / `changes` | cli 2105, 2107; plots.js 1045-1047, digital.js 342-344 | complies - `"1"` on both sides, and both now force `decode` with `changes` |
| `deadband` | cli 2109; plots.js 1049, digital.js 346 | complies - the same `name=value,...` text (the UI trims) |
| `limit` | cli 532, 566, 1824; exportdlg.js `?limit=200`; api.js history paging | complies - integer |
| `bus` | cli 1785, 1830 | complies - one builder shape, two call sites |
| `token` | api.js 522 (`?token=` on /ws) against the `Authorization` header everywhere else | exempt - SPEC 3.2 specifies the query form for the WebSocket, which cannot carry a header |

One note on the registry entry as written: "more than one builder is a violation unless both call one helper" cannot be satisfied across the Python/JS boundary, where sharing a helper is impossible, so every cross-language pair would be filed a violation and the class would stop discriminating.
The operative test used here is the one in the entry's own parenthesis: the forms must agree (repeated against comma-joined, encoded against raw, ISO against epoch).
Worth narrowing the wording to that before the next round runs it.

## Sweep: class 55, a refusal keyed on a kind name where the behaviour keys on a rendering property

`grep -rn '\bkind ==\|\btype ==\|\.kind\b\|\.type\b\|\["kind"\]\|kind:'` over `server.py`, `cli_output.py`, `protocol.py`, `webui/*.js` = **48 sites** (`render.py` holds none).

| sites | what | verdict |
|---|---|---|
| server.py:2947 `_renders_as_label` | the deadband refusal | **violates** - keyed on the name's last-declared kind, guarding a path (`_render`) that keys on the `(name, sid)` entry of `_decode_map`; a row from another stream renders numerically and is still refused (F4) |
| server.py:2959, 2962 `_decode_map` | where the labels are assigned | complies - this is the site that *defines* the property the refusal should test |
| server.py:2695 | `kind: str` in `export_filename` | exempt - a filename prefix, no branch |
| protocol.py:624, 878, 883, 990, 997, 999, 1005 | `!pd` parsing and `channel_meta` construction | complies - the kind vocabulary is closed at `analog`/`enum`/`bits` here, and both labelled kinds are handled at each site |
| protocol.py:354, 356 | `kind == "OK"` / `"ERR"` in the response envelope | complies - the branch and the property are the same thing (the token on the wire) |
| cli_output.py:325, 329 | `ch.kind == "bits"` / `"enum"` in the CLI decoder | complies - mirrors `_decode_map`; `analog` is the only other kind and is numeric |
| plots.js:221, 224, 272 | decode and routing of a sample to the digital lanes | complies - `enum` and `bits` are exactly the labelled kinds; `analog` stays on the charts |
| plots.js:334, 345, 348 | `channel.kind === "bit"` mapping `channel_meta` onto lane kinds | complies - the two labelled kinds map, everything else becomes `analog` |
| plots.js:506 | `c.type !== "f4" && (scale === null || Number.isInteger(scale))` | complies - the property (can this channel show a fraction) is exactly what is tested |
| digital.js:142, 144 | `isBit = ch.kind === "bits"`, lane construction | complies |
| digital.js:380, 413, 542 | `LANE_KINDS[lane.kind]` dispatch | complies - reachable only through plots.js:272, which admits the two lane kinds; an unknown kind would throw rather than slip through |
| digital.js:494 | `type === "mouseleave"` | exempt - a DOM event name |
| exportdlg.js:50, 62 | `f.type === "check"` / `"select"` | exempt - the dialog's own field vocabulary; an unknown type renders as text and gates no refusal |
| can.js:504, terminal.js:505, digital.js:328, plots.js:1029, exportdlg.js:197 | `kind:` in the export-dialog context | exempt - names the download file, no branch |
| chrome.js:51, exportdlg.js:52, settings.js:227, 235, 241, 350, 356, 363, 369 (9 sites) | `element.type = "..."` | exempt - DOM property assignment |

## The two questions

**1. What am I least confident about here, and re-driven.**
Whether the seams I ruled "consistent" by reading were consistent in a running process.
The one I re-drove rather than re-read was the 503 seam, because it is the only fix in the round whose test substitutes a *sequence* (calling `store.stop()` on a serving app) for the one the user gets, and that is precisely where F1 was: the fix is correct in the unit and inert in production.
The revert re-checks are the second candidate, and the first pass of R10-R12 was wrong in the safe-looking direction (`| tail` masking node's exit status) until I read the `# fail` counts instead; the corrected runs caught all three.
Not covered by this leg and stated rather than counted as done: anything needing Windows (C6/C10's `newline=` behaviour, the `.DB` case-fold), a second board (the port `target` and lines/s chips), and the browser checklists both web UI batches filed.

**2. What should we have checked that we have not thought about.**
The other direction of the version skew that C3 opened.
C3 asks "would an older daemon drop this parameter"; the round asked it of `until_ts` and stopped, while the same diff moved `decode`, `changes`, `deadband`, `port` and `format=csv` onto queries older daemons do not declare (F2).
The second gap is the one the class-53 sweep made visible and did not close: the web UI is exempt only because it ships with its daemon, which means the exemption silently depends on a rule nothing enforces - no test asserts that the served UI and the serving daemon are the same build.
The third is authentication as a *property* rather than a stored value (F3): `!authToken` is used as a proxy for "this daemon is open", and the same conflation would reach anything else that branches on the token being set.
