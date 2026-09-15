# Fix: browser leads 1 and 2 (ids)

Scratch scripts, logs and downloads: `~/tt-data/browser-leg/leads/` (`t_lead1_ids.py`, `revert_verify.py`, `host_probe.mjs`, `logs/`).

## Changes

Daemon:

- `host/mcuscope/server.py:1864`: `/plot/export` takes `since_id`, declared as on `/lines` (`le=MAX_LINE_ID`, no lower limit).
- `server.py:1905-1909`: folded into the scope as `id_from = max(since_id + 1, session start_id)`.
  - Clamped at `MAX_LINE_ID - 1` first, so `since_id=2**63-1` binds as an SQLite integer instead of raising a 500.
  - Every store call already receives `**win.scope`: the stream check, the decode anchor and the rows all see it.

Web UI, lead 1:

- `exportrange.js:59`: a shown window carrying `idTo` sends `since_id` and `id_to = min(watermark, idTo)`, and no `since_ts`/`until_ts`.
- `exportdlg.js:271`: `ctx.shown` may be a function of the option values, so the lanes' window follows the Port choice.
- `plots.js`:
  - Each sample's line id travels in `x` (`:269` live, `:318` seed).
  - `chart.ids` runs parallel to the x arrays: push `:561`, null for a reset's gap point `:550`, block trim `:598`, pause snapshot `:1209`.
  - `chartShownWindow` (`:1237`) returns `{sinceId, idTo}` from the first and last drawn samples under **every** time base, skipping gap points.
- `digital.js`:
  - `laneIds` (`:47-52`) holds (drawn tick, line id) for every lane sample, fed from `digitalIngest` (`:103`, `noteLaneId` `:125`).
    It is capped at PLOT_CAP, snapshotted at pause (`:152`), cleared with the lanes (`:848`), and born frozen-empty while paused.
  - `digitalShownWindow(port)` (`:405`): the host base is unchanged (ts edges); the tick branch returns the smallest first id and largest last id over that port's streams.
    `hostAtTick` is gone (nothing else used it).
  - `exportDigital` (`:441-455`) takes each port's window at open; a port with nothing inside exports an empty range (`since_id = id_to = watermark`); no window on any port means the shown mode is not offered.

Web UI, lead 2:

- `plots.js:406-413`: `seedGroup` sorts the group by `plotDefs.get(port|sid).byName` key order; a name the definition lacks goes last.
- `chrome.js:14-16`: the comment now says only a saved override survives a reload.

Deleted: resetting the lane index snapshot on resume. The next pause always overwrites it and nothing reads it while live, and no test could catch removing it.

### Where this departs from the leads report

- **Index per stream, not per port.** The history seed feeds a port's lane streams one after another, so a per-port index goes backwards in tick at each stream boundary.
  With the non-decreasing bump that flattens the later stream to one tick.
  The test "two seeded lane streams on one port" fails with the per-port form (revert P9).
- **Chart host base fixed too** (evidence below). The lanes' host base stays on ts: lanes keep each sample's raw ts, and every host-base lanes run matched.
  - Residual, not fixed: `pushVertex` nudges a lane vertex 1e-4 s when two transitions of one lane share a timestamp.
  - An edge inside that 0.1 ms slot can miss or add one row.
- **Behaviour change:** a host-base chart zoom that holds no sample no longer offers the shown window (it used to export an empty ts window). The tick base already behaved this way.
- A chart's shown export now ends at its last drawn sample's id, which is tighter than the watermark when later non-plot lines exist.
- Ceiling (a `ponytail:` comment at `digital.js:50`): a lane window reaching past the capped index exports from its oldest kept sample.
  The lanes' vertex ring can reach further back than the index at high sample rates.
- CLI: `mcu plot export` has no `--id-to` or `--since-id`, and nothing a CLI user holds is a frozen view to bound by id; `mcu lines` / `log export` already take `--since-id`.
  No CLI option and no `AI_GUIDE` change, so no SPEC section 4 line.
- Owner should pick: the seeded **ad-hoc** chart (`!p`, no `!pd`) still orders chips by name.
  `t_lead2.py` shows `sine, noisy, rpm` live against `noisy, rpm, sine` seeded. The seed has no field order for it; the ruling covers `!pd` only.

## Host base, before the fix

- 6 browser runs (`t_lead1_ids.py host`, tail and a random 29 percent zoom): every chart and lanes export matched.
  A random edge rarely lands inside a burst's nudge spread (0.1 ms per sample).
- Deterministic browser case (zoom edges placed 0.55 ms into two bursts, `RUNTAG=before-edge`): **chart extra 39, missing 42**; lanes matched.
- `host_probe.mjs` (JS stub, 10-sample burst straddling a 30 s tail's left edge): 6 samples drawn, 1 exported.

## Tests

- `host/tests/test_plot_export_since_id.py` (new, 14 cases):
  - a burst split by id that ts cannot split;
  - negative and zero `since_id`;
  - `x`, `1.5`, empty, `0x10` and `2**63` refused with a `since_id: ` error;
  - `2**63-1` gives a header-only 200;
  - at or above `id_to`;
  - intersections with `session` (both ways) and `since_ts` (both orders);
  - a wide export whose other-stream rows sit before the cursor.
- `host/tests/test_webui_js.py`: six `/plot/export?since_id=` guard URLs. The double `exportdlg_guards.mjs` declares `since_id` in daemon order.
- `host/tests/webui_js/fix_browser_ids.test.mjs` (new, 17). Chart:
  - tick-base zoom with bursts straddling both edges (chart and lanes);
  - host-base zoom edges inside nudge spreads;
  - host-base tail burst at the left edge;
  - ring trim before pause;
  - a reset's gap point on each edge;
  - a zoom across a reset (chart and lanes).
- Same file, lanes:
  - two seeded streams on one port;
  - min first and max last id across a port's streams;
  - two ports, a Port choice with nothing inside, and interleaved ids;
  - snapshot surviving a trim while paused;
  - a stream born paused (also not offered);
  - the cap ceiling;
  - a zoom holding no sample;
  - a late tick;
  - duplicate ticks on the right edge.
- Same file, seed order: `!pd` order for chips, lanes and consecutive colour slots with an unknown name last, plus the no-definition control.
- `exportrange.test.mjs`: the id path (no ts, tighter of watermark and last id, other modes, no watermark).
- Updated to the id contract, not weakened:
  - `export_paused_window`, `exportdlg` (ring trim while paused), `prerelease_fixdiff_webui` (FW-2's `selected()` models `since_id`);
  - `prerelease_webui-panes_plots`, `rulings_panes_export`: the interpolation tests are replaced by id assertions.

## Revert results

Each branch was hand-reverted from a copy, the touched suites run, then restored and hash-checked (`revert_verify.py`, `logs/revert_verify.out`).

| Branch | Failing | A failure unique to it |
|---|---|---|
| S1 since_id fold | 6 py | burst split: `[3]` |
| S2 clamp | 2 py | `...last_storable_id...not_a_500` |
| S3 intersect session | 1 py | `..._session_whichever_is_tighter` |
| S4 `le=MAX_LINE_ID` | 2 py | refused `[9223372036854775808]` |
| G1 double's since_id | 1 py | guard double disagrees |
| E1 ids replace ts | 29 js | `a time edge cannot split a burst` |
| E2a no watermark | 1 js | `with no watermark the last id is still the upper bound` |
| E2b tighter id | 13 js | `rows after the last drawn sample are not shown` |
| X1 shown(values) | 24 js | tick-base burst straddle, deep-equal |
| P1 live x.id | 25 js | `null !== '1'` |
| P2 seed x.id | 1 js | seeded streams: shown not offered |
| P3 gap null id | 3 js | `the fixture has no gap point` |
| P5 trim ids | 2 js | ring trim: `'415' !== '4512'` |
| P6 frozen ids | 34 js | TypeError across paused exports |
| P7 xs per base | 6 js | host-base zoom: shown not offered |
| P8a gap skip, first | 1 js | gap point on the left edge |
| P8b gap skip, last | 9 js | gap point on the right edge |
| P9 stream key | 1 js | `stream 2's last sample inside is the later id` |
| P10a seed sort | 1 js | `!pd` order deep-equal |
| P10b unknown last | 1 js | `!pd` order deep-equal |
| P11 span per base | 1 js | host-base tail burst |
| D1 note id | 14 js | lanes: shown not offered |
| D2 born paused | 1 js | `a window only post-pause samples fill is not shown` |
| D3 non-decreasing | 1 js | `the first sample at or after 1015 in arrival order` |
| D4 cap | 1 js | ceiling `'100' !== '4197'` |
| D5 snapshot | 2 js | snapshot test: not offered |
| D7 clear | 9 js | snapshot test `'1000' !== '100'` |
| D8 port filter | 1 js | `a port with nothing inside must not export its whole history` |
| D9 frozen read | 1 js | snapshot test: not offered |
| D10 dup right edge | 1 js | `'104926' !== '104927'` |
| D11 empty stream | 7 js | two ports deep-equal |
| D12a min first | 2 js | `'1043' !== '1042'` |
| D12b max last | 1 js | `'1156' !== '1157'` |
| D13 empty range | 2 js | `a port with nothing inside...` |
| D14 offer only with a window | 2 js | `an empty id range would export nothing, silently` |

D2, D8 and D12b were not caught on the first pass. The tests were sharpened: not offered in a post-pause-only window, interleaved port ids, and a stream layout where last-writer-wins differs from max. All three now fail on revert.

## Browser rerun (fixed code)

`t_lead1.py` reads `since_ts` from the URL and cannot parse an id export. `t_lead1_ids.py <base>` is the same harness with the expected set built from the drawn window, plus an "edge" case that puts both zoom edges inside bursts.

| Base | tail chart | tail lanes | zoom chart | zoom lanes | edge chart | edge lanes |
|---|---|---|---|---|---|---|
| tick | MATCH | MATCH | MATCH | MATCH | MATCH | MATCH |
| host | MATCH | MATCH | MATCH | MATCH | MATCH (was 39 / 42) | MATCH |

- `t_lead2.py` (`logs/t_lead2-fix.out`), in-stream order now matches live: `s0` is `tri, ramp, ftest` (was `ftest, ramp, tri`); gpio lanes are `led, irq, pwm_en` (was `irq, led, pwm_en`).
- Still DIFFERENT as ruled: colour slots and cross-stream order (`state` lane, chart stack) follow first sight. The ad-hoc chart order is flagged above.
- No console or page errors. Every PID started (in `pids.txt`) is confirmed gone.

## Suites

- `uv run python -m pytest -q`: **2038 passed, 1 skipped** (373 s). `ruff check .`: clean.
- `node --test`: 869 pass, 0 fail.

## Proposed CHANGELOG

Fixed:

- Web UI: "Shown window" export from a paused chart no longer drops or adds the samples of a serial burst at either edge, under the tick base and under a host-base zoom; charts, and the lanes under the tick base, now export by line id.
- Web UI: after a reload, a stream's chips, lanes and their colours within that stream follow its `!pd` field order instead of alphabetical order.

Added:

- `/plot/export` accepts `since_id` (exclusive), as `/lines` does.

## Proposed SPEC lines

3.4, beside the `id_to` paragraph (`SPEC.md:851`):

- "`/lines`, `/lines/export`, `/can/frames`, `/plot/series` and `/plot/export` accept `since_id=<line id>`, an exclusive lower bound; it intersects every other bound given."

9.1, replacing `SPEC.md:1603-1605`:

- "The panel's shown window, offered only while that panel is paused and only when it draws a sample. While a drag zoom stands (9.2) that is the zoom range."
- "A chart sends the line ids of the first and last samples it draws, as `since_id` / `id_to`, under every time base: the samples of one serial burst share one timestamp."
- "The lanes send their host-time edges as `since_ts` / `until_ts`; under the tick base, the line ids of the first and last samples of the exported port inside the window, and a port with none exports nothing."
- "A terminal pane sends its host-time edges and its first row's id as `since_id`."

9.2:

- `SPEC.md:1701`: "`GET /plot/export?names=&last_ms=&since_id=&since_ts=&until_ts=&id_to=&format=long|wide&port=&decode=&changes=&deadband=`".
- Optional, near `SPEC.md:1742`: "A stream restored by the history seed lists its chips and lanes in its `!pd` field order."
