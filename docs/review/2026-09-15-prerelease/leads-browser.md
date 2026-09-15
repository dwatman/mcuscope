# Leads from the browser leg (leads)

Scripts and logs: `~/tt-data/browser-leg/leads/` (`t_lead1.py`, `burst_board.py`, `t_lead2.py`, `logs/`, `dl/`).

## Lead 1: shown-window export under the tick base: REAL (chart and lanes)

Setup: fake board, one sample per ms on streams 0 (`a,b`) and 1 (`gpio` bits), stream 2 enum every 4 ms.
Samples go out in bursts at random 5 to 60 ms intervals, so the daemon stamps each burst with one `ts` (`serial_link.py:584-587`).
Tick base, 5 s window, pause all, export "Shown window" from the dialog.
Expected rows are the same download URL with `since_ts`/`until_ts` removed (`id_to` kept), filtered to ticks inside the drawn window, so format and decode match.

| Case | Surface | Extra (outside window) | Missing (inside window) |
|---|---|---|---|
| tail, 1 kHz bursts | chart (wide) | 0 | 18, ticks 13977..13994 |
| tail, 1 kHz bursts | lanes (long) | 0 | 40 rows, ticks 13977..13994 |
| zoom 1.45 s, 1 kHz bursts | chart | 23, ticks 26180..26202 | 42, ticks 24729..24770 |
| zoom 1.45 s, 1 kHz bursts | lanes | 52 | 94 |
| control, 50 ms, 1 sample per burst | chart tail / zoom | 0 / 0 | 0 / 0 |
| control, 50 ms, 1 sample per burst | lanes tail / zoom | 0 / 0 | 0 / 2, tick 24254 = zoom max |

Causes:

- Chart: `chartShownWindow` (`plots.js:1238`) sends `xsHost[first]` / `xsHost[last]`, which `addSample` has nudged by 1e-4 per colliding sample (`plots.js:546`).
  - Tail case: `since_ts` sat 2.2 ms above the first in-window sample's stored `ts` (22 burst mates), so the whole burst's in-window part is dropped.
  - Right edge: `until_ts` at or above the burst's `ts` takes the rest of the burst past the window.
  - This already deviates from SPEC 9.1 (`SPEC.md:1604`, "the host times of the first and last samples drawn").
- Lanes: `hostAtTick` (`digital.js:392`) interpolates host time between vertices; host time is not linear in tick, so the edge lands on either side of a burst.
  - The 50 ms control shows it fails without bursts too: the zoomed `until_ts` came out 80 us below the stored `ts` of the last in-window sample.
- Even exact stored `ts` cannot separate samples of one burst, which SPEC already concedes for the terminal (`SPEC.md:1605`, "a serial burst shares one timestamp", hence `since_id`).

Fix (both sides; ids, not ts or ticks):

- Daemon: `/plot/export` accepts `since_id` (exclusive, as `/lines` and `/plot/series` do).
  - `server.py:1860` parameter list; fold into the scope at `server.py:2772` as `id_from = max(session start_id, since_id + 1)`.
  - The store already takes `id_from` (`store.py:2348`), and `export_sids_safe` / `first_export_line_id_safe` receive `**win.scope`, so nothing below changes.
- Chart: keep a per-sample line id array parallel to `xsTick` (push in `addSample` `plots.js:522`, snapshot in `chart.frozen` `plots.js:1198`, trim with the ring).
  - Tick branch of `chartShownWindow` (`plots.js:1234-1238`) returns `{ sinceId: ids[first] - 1, idTo: ids[last] }` and no `ts` bounds.
  - `exportrange.params` (`exportrange.js:64-69`) sends `id_to = min(watermark, idTo)`.
- Lanes: they store transitions only, so they need a capped per-port index of (drawn tick, line id) for every lane sample, fed from `digitalIngest` (`digital.js:55`; `routePoints` `plots.js:285` has to pass `row.id`).
  - Tick branch of `digitalShownWindow` (`digital.js:385-386`) then returns id bounds; `hostAtTick` goes.
  - Ceiling: lanes span streams, and ids are arrival order, so a board emitting streams out of tick order leaves a few edge rows of the other stream inside the id range.
- A tick-bounded query (`since_tick`/`until_tick`) is the weaker option: raw ticks restart and wrap (SPEC 9.2 time base), while the page's axis is continued ticks.
- Proposed SPEC lines:
  - 9.1 (`SPEC.md:1604`): "Under the tick base these are the line ids of the first and last samples drawn, as `since_id` / `id_to`."
  - 3.4 (`SPEC.md:851`) and 9.2 (`SPEC.md:1701`): `/plot/export` accepts `since_id`.

Not driven: the host-base branch (`plots.js:1232`) measures from the nudged `xs[n - 1]` too, so a bursty host-base tail export likely has the same edge error.

## Lead 2: seeded chart chip order: REAL (order, colours and stack order all change on reload)

Driven with `mcu-sim --plot`: page open on an empty capture, sim started (live build), sim stopped, page reloaded (seed is the only builder). No saved colours.

| Surface | Live | After reload |
|---|---|---|
| `s0` chips | tri, ramp, ftest (slots 3, 4, 5) | ftest, ramp, tri (slots 0, 1, 2) |
| ad-hoc chips | sine, noisy, rpm (slots 0, 1, 2) | noisy, rpm, sine (slots 6, 7, 0) |
| lanes | state, led, irq, pwm_en | irq, led, pwm_en, state |
| chart stack | adhoc, s0 | s0, adhoc |

Order source: `store.py:2108` returns `/plot/channels` sorted by name; `api.js:355` stable-sorts by `last_ts`, and fields of one line tie, so they stay alphabetical.
`plotSeed` groups in that order and `mergeSeedSeries` (`plots.js:413`) inserts each row's points in group order, so `addChannel` sees names alphabetically.
Colours follow: `renderChans` calls `colorFor` in `chart.names` order (`plots.js:699`), and `colorFor` hands out slots on first sight (`chrome.js:44-53`).

SPEC: 9.2 specifies no chip order (`SPEC.md:1742`) and says slots go "per name on first sight" (`SPEC.md:1750`), so no SPEC clause is broken.
It is still a defect: the order is an accident of a name sort, the declared `!pd` order is known to the page, and `chrome.js:14-15` claims "a name keeps its colour across ports and reloads", which holds only for a saved override.

Smallest fix: in `seedGroup` (`plots.js:401`), before `mergeSeedSeries(group)`, sort `group` by each name's position in `plotDefs.get(port + "|" + sid).byName` (key order is field order, lanes included), unknown names last.
`seedPlotDefs` primes `plotDefs` before the seed runs (`api.js:526`).
This restores in-stream chip and lane order and the slot order within a stream.

Owner should pick: cross-stream order (chart stack, lanes across streams) and colour slots still follow first sight, which live arrival and a seed cannot reproduce alike.
Reload-stable colours need a deterministic slot (for example by (port, sid, field) order, or a name hash), which changes the SPEC 1750 rule; otherwise fix the `chrome.js:14-15` comment.

No console errors or page errors in any run. Every process started (6 PIDs in `pids.txt`) is stopped.
