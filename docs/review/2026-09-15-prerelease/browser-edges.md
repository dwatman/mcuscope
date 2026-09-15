# Browser leg: edges (fake boards on socket://)

Headless Chromium (Playwright 1.62.0), daemon without `--sim`, a Python fake board per item.
Scripts, outputs and daemon logs in `~/tt-data/browser-leg/edges/` (`harness.py` holds the board, daemon and page helpers).

## Results

- PASS: D-10. Soloed `v` of `!pd 0 v:u2:mV i:u2:mA`: `uplot.axes[1].label` is `mV`, then `V` 0.2 s after `!pd 0 v:u2:V i:u2:mA`; the chip reads `V`, and the solo holds. Script: `d10.py` (`d10.out`).
- FAIL: Two boards on one port under the tick base. Charts pass: each is anchored on its own board's newest tick with no break or epoch. Lanes fail: the lower-uptime board's lanes draw a full-width held level, not nothing. Script: `twoboards.py` (`twoboards.out`).
- PASS: 2^32 tick wrap, seeded at 0xFFFFF000. One all-null point in the chart and one null vertex in the lane. The x step across the wrap is 50 ms (offset 2^32 - 6 ms), and `scales.x` stays anchored past 2^32 with a 30000 ms span. Script: `wrap.py` (`wrap.out`).
- PASS: 64 lanes live with one stream quiet. Every lane repaints on every 5 Hz tick, the quiet stream's too; CPU is about 2x the 8-lane run (numbers below). There is no threshold. Script: `perf.py` (`perf.out`).

## 64 lanes: numbers

The setup:

- 8 streams, 20 Hz each, with random bytes. K bit lanes per stream: K=8 gives 64 lanes, K=1 gives 8, so traffic is identical.
- Stream 7 goes quiet 2 s before the 30 s window.
- Viewport 1600x1400; each lane canvas is 230 px wide.
- With 64 lanes, 31 lanes sit in the scroller's view and 33 below it. `document.hidden` is false.

| run | ScriptDuration s / 30 s | TaskDuration s / 30 s | loadavg start, end |
|---|---|---|---|
| 64 lanes, run 1 | 0.739 | 4.323 | 1.21, 1.40 |
| 8 lanes, run 1 | 0.361 | 2.331 | 1.29, 0.79 |
| 64 lanes, run 2 | 0.747 | 4.361 | 0.73, 1.00 |
| 8 lanes, run 2 | 0.358 | 2.264 | 1.08, 1.64 |

- Ratios of means, 64 lanes against 8: ScriptDuration 2.07, TaskDuration 1.89. The pairs agree within 3 percent, and the load stayed under 2, so the noise is low. An earlier 64-lane attempt read 0.715 and 3.877.
- Repaints were counted by wrapping each lane context's `clearRect` (once per `drawDigitalLane`) and grouped per tick by the ruler's redraw.
  - Each lane got 150 repaints in 30 s, about 150 ticks, and every tick repainted every lane.
  - That includes the quiet stream's lanes and the 33 lanes below the scroller's visible area.
- The quiet lanes repaint because `lane.drawnEdge !== xmax`: the shared right edge moves with the other streams, so the lanes scroll.
- Control: with every stream quiet, the lanes got 0 repaints in 3 s, so the counter distinguishes an idle tick.
- Lanes below the fold repaint just as often, since `redrawDigital` skips only lanes of width 0. That costs work with nothing visible to show for it.

## Defects

### Two boards on one port: the lower-uptime board's lanes are drawn, not off screen

- Repro:
  - One TCP link carries interleaved lines from board A (uptime 5,000,000 ms: sid 0 analog `va`, sid 1 bits `a0,a1`) and board B (uptime 10,000 ms: sid 2 `vb`, sid 3 bits `b0,b1`), 20 Hz each, with the bits toggling.
  - Switch to the tick base and wait 4 s.
- Observed:
  - The shared lane edge is A's tick (5005850), and the window is 4975850..5005850.
  - B's lanes (last vertex at tick 16350) still yield one segment each from `laneSegments`, the newest level held to the right edge.
  - Recorded draw calls for `b0`: `fillRect(0,8,230)`, `moveTo(0,8)`, `lineTo(230,8)`; for `b1`: `moveTo(0,26)`, `lineTo(230,26)`.
  - So each B lane shows a flat level across the whole lane, which reads as a constant signal while B toggles at 20 Hz.
  - Re-run twice, the same every time.
- Expected (SPEC 9.2): "under the tick base a board with less uptime draws its lanes off screen", so no waveform for B.
- Cause: `laneSegments` extends the newest vertex to the window's right edge whatever its distance behind it. That is right for a held level on one clock, not for another board's clock.
- Owner should pick: clamp that hold (e.g. at the port's own newest sample), or change SPEC 9.2's wording to "draws a held level".
- Setup note: the two boards use distinct sids and names, because SPEC 2.5 requires unique names on a port.
  - Each chart and lane therefore sees one monotonic clock, and no tick epoch opens (all offsets 0).
  - Two boards sharing names would merge into one series, which SPEC already calls incoherent.

## Notes

- No console errors or page errors in any run.
- A chart restored by the history seed lists its chips in `/plot/channels` order (`i, v`), not the `!pd` order (`v, i`).
