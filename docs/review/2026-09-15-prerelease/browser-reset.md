# Browser leg: reset

Headless Chromium (playwright 1.62.0) against `mcuscoped` without `--sim`, one `[[ports]]` entry on `socket://127.0.0.1:993x`.
The board is a Python fake (`~/tt-data/browser-leg/reset/board.py`).
It speaks ping/info and sends `!pd`/`!ps` streams 0 (analog `cnt`, `sq`), 1 (bits `led`, `irq`) and 2 (enum `mode`) at 20 Hz, plus CAN id 100 at 10 Hz.
Values are derived from the raw tick. SIGUSR1 restarts the tick at 0 on the open link (a board reset), and SIGSTOP silences it with the link kept open.
Scripts and logs are in `~/tt-data/browser-leg/reset/`; each item's output is in `logs/<script>.out`, and the CSVs are in `dl/`.

## Results

- PASS: Tick reset.
  - Break: after a reset from tick 67925 to 0, `u.data` holds exactly one all-null row, between the last pre-reset sample (x 67925, cnt 26792) and the first post-reset one (x 67975.2, cnt 20003). x is monotonic and continues by the 50 ms host gap. Each of the 3 lanes (`led`, `irq`, `mode`) has exactly one null vertex.
  - Scrolling: after the reset, the chart x max moves +1.60 s and the lane edge +1.55 s over 1.5 s.
  - Hover: a hover on post-reset line `!ps 0 18D3` puts the chart cursor on that sample (chip `cnt` 20635 = expected) and shows the lane cursor at drawn tick 74248, past the pre-reset edge.
  - Script: `t_reset.py tick`.
- PASS: Tick reset, chart cursor.
  - Parked with `setCursor` on the gap point's x, the cursor index is the gap row and both chips read `--`. This holds with both channels shown and with `cnt` soloed (y axis drawn).
  - The y scales are unchanged (y0 [19300, 27500], y1 [29900, 31100]) and do not reach 0.
  - Script: `t_reset.py tick`.
  - Note: the gap point sits 1e-4 ms after the last pre-reset sample, about 1e-6 px apart. A real pointer at its pixel reads that pre-reset sample (26792), not `--`, so the check as written cannot be done by hand.
- PASS: Host base, board reset mid-stream.
  - Break: one all-null row in `u.data` between the pre-reset (cnt 26803) and post-reset (cnt 20002) samples, 50 ms apart on the host clock, and one null vertex per lane. This matches SPEC 9.2 "in every time base".
  - Scrolling: +1.61 s / +1.51 s over 1.5 s.
  - Hover: a hover on a post-reset line lands on its own sample (cnt 20640).
  - Script: `t_reset.py host`.
- PASS: E-4.
  - The page started with the port `open_failed`, and the board came up 1.6 s after load. The page saw target `fakeboard` at +8.0 s, while typing was still going on, and the mode toggle moved from raw to cmd.
  - `document.activeElement` stayed `markerInput` over all 35 keystrokes, with the caret at the end.
  - Enter stored marker row id 716 with the full text and cleared the box.
  - Script: `t_e4.py`.
- PASS: D-5.
  - The board was SIGSTOPped for 95 s with the link still connected (`lines_rx` frozen). After a reload, the id 100 age cell read `1m36s` at the first poll of the table, 1.1 s after the reload, against 96.1 s of real silence. It then ticked on to `1m46s`.
  - Script: `t_d5.py`.
- PASS: FW-2. This uses the fake board with known ticks, not a real board. Tick base, pause all, then "Shown window" export.
  - Chart (wide): the drawn x range is [12846, 42846]. The first and last drawn samples are tick 12874 and 42846, and the CSV starts and ends on the same ticks with identical `ts`.
  - Lanes (long, 1794 rows): the window is the same, and the CSV min/max tick of 12874/42846 matches the first and last stream 1/2 samples inside it.
  - Script: `t_fw2.py`.

No console errors or page errors in any run.

## Defects

None.

## Coverage notes

- The lanes' shown-window `since_ts` is interpolated from lane vertices (`hostAtTick`) and landed 28 ms before the first in-window sample, which is within the 50 ms spacing. A faster stream with host receive jitter was not tried, and could put the interpolated edge on the wrong side of a sample.
- All reset checks use an in-link tick restart. A standalone `mcu-sim` restart, which also reconnects the port, was not run.
