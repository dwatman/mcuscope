# Owner requests during the 2026-09-14 implementation round

- Demo: one analog chart (typed stream 0 with units) plus digital/enum lanes; ad-hoc `!p` dropped. Batch A.
- Demo: `ramp` wraps at 256, not 65535. Batch A.
- Demo: fewer CAN ids, about 4 rows over 2 buses, each table feature shown once. Batch A.
- CAN table: DATA wraps 8 bytes over three lines at 3 bytes per line; at the default sidebar width it must fit 4 bytes per line (8 bytes in two lines). Batch B.
- CAN section: shrink to fit its rows instead of a fixed 45% split, so the plots keep their room. Batch B.
