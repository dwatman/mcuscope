# Laptop heat while streaming: findings

Measured 2026-11-20 on the owner's laptop (i7-8550U, 3200x1800 at 2x, 60 Hz, AC), `mcu-sim --plot --flood N`, isolated daemon, web UI "Both" view, headed Firefox 145 (system) and Playwright Chromium.
Other Claude sessions ran throughout: per-process CPU is reliable, package power only as a delta between adjacent windows, the idle floor was not measurable.
Scripts: `~/git/mcuscope-tools/laptop-heat/`.

## Where the load is (steady state, after the first 60-90 s)

CPU in ms per second; W is RAPL package power.

| scenario | Firefox parent | Firefox content | daemon | sim | pkg W |
|---|---|---|---|---|---|
| no client, 2000/s | - | - | 78 | 22 | 5.5-7.9 |
| `mcu tail -f`, 2000/s | (tail 22) | - | 94 | 21 | 9.6 |
| Firefox, terminal live, 1000/s | 92-111 | 87-97 | 71-84 | ~20 | 11.1-12.4 |
| Firefox, terminal shed, 2500/s | 49 | 33 | 114 | ~25 | 9.4 |
| Firefox, terminal shed, 10000/s | 49 | 42 | 335-387 | 56-59 | 12.7-13.8 |
| Firefox minimised, 2000/s (shed) | 8 total | | 99 | 22 | 10.1 |

- The live terminal is the web UI's main cost: about 110 ms/s of Firefox CPU and about 3 W over the shed state.
- The daemon scales with line rate and dominates above the shed threshold.
- The first 30-60 s after page load cost 2-5x the steady figure (backfill and the terminal being fed before the shed trips).

## Findings

1. **The high-rate shed latches on.** `api.js` `tickRate` counts rows by arrival time over a 1 s timer window, trips at 2000/s and releases at 800/s.
   A stall (CPU contention, GC) delivers a backlog burst that reads as over 2000/s; a steady 800-2000/s then never releases it.
   Seen at a steady 1500/s: tripped at 209 s reading 2225/s while other load spiked.
2. **The terminal's repaint rate is the lever.** At these rates every flush replaces a whole screenful, so cost scales with flushes per second.
   - `FLUSH_MS` 33 against 100 showed nothing because /ws delivers about 9 frames/s, so both ran about 9 flushes/s.
   - At 1200/s: 33 ms 191 ms/s of Firefox CPU, 250 ms 121 (about 2 W less), 500 ms about 110.
3. **Content-process cost at 1000/s live (92 ms/s, Gecko profile):**
   - our JS about 32: terminal `render` 18.6, of which 16.8 is a sync reflow forced by `set Element.scrollTop`; uPlot about 7; digital lanes 2.5;
   - paint about 24 (display list build 12, WebRender display list 9), with about 50 WebRender fallback items per second (CPU-rasterised);
   - event-loop wake-ups (`PollWrapper`) 26.
4. **Parent-process cost:** CanvasRenderer 21 ms/s (2D canvas for charts and lanes), WebRender backend 16, Renderer 12, Compositor 7, main 14.
5. **/ws is already well batched:** about 9 frames/s at 1000 and 2500 rows/s (115 and 300 rows per frame); not a cost driver.
6. **Daemon at 10000/s (py-spy, one `mcu tail -f` client):** `store._insert_batch` 49% of samples, serial reader thread 16%, /ws send 10% (JSON 5%, permessage-deflate 4.4%).
   - py-spy counts the SQLite call's I/O waits as busy, so these are shares of wall time, not CPU.
   - Inserts are already one `executemany` per table per batch, and each `lines` index is backed by a measured query win (`store.py` comments); no cheap cut.
   - permessage-deflate off, Firefox client at 2500/s: daemon 116 and 112 ms/s against 106 and 106; power within noise.
7. **CSS is not the lever.** At 1200/s, `contain: strict` on `.scrollback` and `text-overflow: clip` on lines were within noise.
   - The terminal hidden but still laid out saved 30-40 ms/s: that is its paint and composite share; the rest is per-flush DOM and layout.
8. **Headed Chromium froze the page for about 3.5 s of every 4,** with plots shown, even with no flood.
   - A canvas drawn straight from a timer does it on this laptop (ANGLE on Mesa Intel GL); drawn inside `requestAnimationFrame`, or with the GPU off, it does not.
   - /ws arrived in bursts, the shed flapped, and the terminal advanced every 3 s instead of every 1 s.

## Fixed

- b7108ed: finding 1. The rate is measured over the larger of wall time and the rows' capture-time span, and while shed, live panes rebuild once a second (badge "display rate limited").
  - Headed Firefox at 2500/s: newest line advanced every 0.91 s; Firefox 108 ms/s, against 82 for the old full stop and about 190 for the live terminal.
- Finding 8: the 5 Hz plot timer only requests a frame; the draw runs in it. Headed Chromium at 2500/s: no stall (timer gaps at most 253 ms), shed held throughout, newest line every 0.91 s. Firefox unchanged (171-174 against 170-171 ms/s).
- Finding 2 (owner pick: adaptive): from 200 lines/s the terminal flushes every 250 ms, below that every 33 ms.
- Finding 6 (owner pick): /ws compression off on a loopback bind, kept for a network bind.
- The three together, headed Firefox at 1200/s, terminal live throughout: Firefox 196 and 191 ms/s down to 123 and 120, daemon 78 to 72, package 13.8 and 12.6 W down to 10.7 and 10.2 W.

## Native client (plan Step 4)

Not worth pursuing.
- After the fixes, Firefox at 1200/s costs about 120 ms/s, against 22 for `mcu tail -f`, which draws no charts or CAN table.
- The remaining excess is canvas charts and text paint, which a Qt client would also pay; above about 2000/s the daemon's SQLite writes dominate either way.

## Not covered

- Chromium minimised: the window-manager minimise did not set `document.hidden`, so not measured.
- The idle floor: other sessions ran throughout.
