# Live screenshot observations (dark.png, light.png, empty.png)

Captured against `mcuscoped --sim` on 8799 and an empty daemon on 8798, Firefox kiosk at 1600 px width, sidebar at its 360 px default.

## Seen in the shots

- Plot window buttons overflow at the default sidebar width: the ad-hoc chart header shows "5n" where "5m" is clipped (light.png, ad-hoc header). S.
- Owner-flagged: with two analog plots the digital / enum panel is off screen in the default view; the sidebar must be scrolled to find it, and nothing hints it exists. Options: cap each analog chart's height so N charts plus the digital panel fit, give the digital panel its own collapsible section pinned below the charts, or a widget strip (chips per widget) at the top of the Plots section that scrolls to and toggles each. M.
- Three plot widgets stacked in a 360 px column each take about 250 px, so the third is below the fold as well. M.
- The uPlot legend under each chart reads "Value: -- ftest (degC): --" in two rows when no cursor is over the chart. It costs 50 px per chart for dashes. Collapse to one row of swatch + name + value, or hide values until hover. S.
- Mixed-unit series share one y axis: degC, mA and V on "stream 0", where the ramp in mA flattens the others (light.png). A per-unit axis or a per-series normalise toggle would make the typed streams readable. M.
- Plot titles are protocol names: "stream 0", "ad-hoc (!p)". The user thinks in board names. S if the firmware declares a name, otherwise editable title stored per browser.
- CAN DATA cell wraps 8 bytes over three lines at the default width, so the extended-id row is three rows tall and the table jumps in height as payloads change. Consider a fixed two-line layout or shrinking the COUNT and MS columns. S.
- CAN column headers "MS" and "AGE" are cryptic. "MS" is the inter-frame period. Rename to "period" or add a title tooltip. S.
- The daemon chip repeats the word "sim" three times: "sim sim://demo sim 105/s" (port alias, device, per-port rate label). S.
- When only one port is attached, every terminal line still carries the port tag column "sim", which is a column of noise. Hide the port column when one port is attached, show it when a second appears. S.
- The pane counter "1 / 5" next to "+ pane" is unexplained. "1 of 5 panes" or a tooltip. S.
- The prompt glyph changes between ">" (cmd) and "$" (raw). The meaning is not discoverable and the toggle already says cmd/raw. Either drop the glyph change or add a title. S.
- The line-ending select reads "port default (lf)"; "timeout 1000 ms" and "marker text / Marker" sit inline. On the empty daemon the timeout box hides in raw mode, so the bar reflows when the mode changes. S.
- The empty daemon (empty.png) shows a sys line and a session marker and nothing that says what to do next. The terminal empty state should read "No ports attached. Attach a serial port to start capturing." with the attach action, since "+ Attach" is the only affordance and it is in the far corner. S.
- The CAN and plot empty states name protocol strings ("!can events", "!p / !pd / !ps events"). A newcomer with a plain console does not know the monitor protocol; say what the board has to print and link to the firmware doc. S.
- The Digital / Enum header is visible with a live "pause" button while it has no lanes; the export button is dimmed but pause is not. Hide the header until the first lane arrives, matching the analog empty state. S.
- The command input is enabled with no port attached and the port select shows "auto". Typing and pressing Enter should say "no port attached" up front, or the input should be disabled with that placeholder. S.
- Light theme holds up: contrast and channel colours read fine, the accent primary button is legible. The lighter theme makes the six-colour terminal (blue port tag, purple EVT, grey DBG) busier than dark; the port tag could drop to text-dim.
- Status bar: "web ui" version-slot text next to the brand is filler. The daemon chip carries version, address, uptime, db size and rate as one grey mono string; the address is the only thing a user needs to copy. Consider putting uptime and db size in the chip tooltip.

## Daemon side, found while setting up

- A config with `db_path` at top level instead of under `[storage]` started with no warning and wrote to the default database. The startup log shows nothing about the ignored key. A warning for unknown top-level keys would have caught it. S.

## Good, keep

- Channel filter chips with per-channel colour that matches the line tags.
- The marker divider line across the terminal.
- Byte-change highlight in the CAN table.
- Stream and rate warnings as pills in the status bar rather than modals.
- The "live" pill and jump-to-latest button.
