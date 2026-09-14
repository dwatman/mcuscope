# Owner requests during the 2026-09-14 implementation round

- Demo: one analog chart (typed stream 0 with units) plus digital/enum lanes; ad-hoc `!p` dropped. Batch A.
- Demo: `ramp` wraps at 256, not 65535. Batch A.
- Demo: fewer CAN ids, about 4 rows over 2 buses, each table feature shown once. Batch A.
- CAN table: DATA wraps 8 bytes over three lines at 3 bytes per line; at the default sidebar width it must fit 4 bytes per line (8 bytes in two lines). Batch B.
- CAN section: shrink to fit its rows instead of a fixed 45% split, so the plots keep their room. Batch B.
- Demo chart: tri (1 Hz) and ftest (0.5 Hz) are dense at the 30 s window; slow them so the one chart reads cleanly. Batch C.
- Demo digital lanes: pwm_en (200 ms) renders as a solid block and the state enum as dense crossings at the 30 s default window; slow the demo signals so each lane reads at 30 s. Batch C.
- CAN data: at least 4 bytes a line; wrap points before bytes 5 and 7 (8, 6 + 2, or 4 + 4). Done by orchestrator.
- Command bar line-ending select: only as wide as none/LF/CRLF. Done by orchestrator.
- Empty states everywhere (CAN, plots, digital, terminal panes): one line; detail goes in the tooltip. Batch C.
